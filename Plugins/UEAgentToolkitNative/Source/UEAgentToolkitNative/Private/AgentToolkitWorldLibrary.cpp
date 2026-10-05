#include "AgentToolkitWorldLibrary.h"

#include "ActorEditorUtils.h"
#include "Animation/MovieScene2DTransformSection.h"
#include "Animation/MovieScene2DTransformTrack.h"
#include "Animation/WidgetAnimation.h"
#include "Blueprint/WidgetTree.h"
#include "Channels/MovieSceneFloatChannel.h"
#include "Components/Widget.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "EnvironmentQuery/EnvQuery.h"
#include "EnvironmentQuery/EnvQueryGenerator.h"
#include "EnvironmentQuery/EnvQueryOption.h"
#include "EnvironmentQuery/EnvQueryTest.h"
#include "IImageWrapper.h"
#include "IImageWrapperModule.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Landscape.h"
#include "LandscapeInfo.h"
#include "LandscapeDataAccess.h"
#include "LandscapeEdit.h"
#include "LandscapeEditLayer.h"
#include "LandscapeLayerInfoObject.h"
#include "LandscapeUtils.h"
#include "Materials/MaterialInterface.h"
#include "Misc/App.h"
#include "Misc/FileHelper.h"
#include "Modules/ModuleManager.h"
#include "MovieScene.h"
#include "RHIStats.h"
#include "DynamicRHI.h"
#include "RenderTimer.h"
#include "Sections/MovieSceneColorSection.h"
#include "Sections/MovieSceneFloatSection.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Tracks/MovieSceneColorTrack.h"
#include "Tracks/MovieSceneFloatTrack.h"
#include "WidgetBlueprint.h"

namespace AgentToolkitWorld
{
	static FString Fail(const FString& Message) { return TEXT("ERROR: ") + Message; }

	static FString ToJson(const TSharedRef<FJsonObject>& Object)
	{
		FString Out;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Object, Writer);
		return Out;
	}

	/** Runtime EQS edits invalidate the editor graph; dropping it makes the EQS editor rebuild it from the asset. */
	static void Touch(UEnvQuery* Query)
	{
		Query->Modify();
#if WITH_EDITORONLY_DATA
		Query->EdGraph = nullptr;
#endif
		Query->MarkPackageDirty();
	}

	static bool LoadHeights(const FString& File, int32 SizeX, int32 SizeY, TArray<uint16>& Out, FString& Error)
	{
		TArray<uint8> Bytes;
		if (!FFileHelper::LoadFileToArray(Bytes, *File))
		{
			Error = FString::Printf(TEXT("cannot read heightmap '%s'"), *File);
			return false;
		}
		const FString Ext = FPaths::GetExtension(File).ToLower();
		int32 W = 0, H = 0;
		TArray<uint16> Src;
		if (Ext == TEXT("r16") || Ext == TEXT("raw"))
		{
			const int32 Count = Bytes.Num() / 2;
			W = H = FMath::RoundToInt(FMath::Sqrt(static_cast<float>(Count)));
			if (W * H != Count)
			{
				Error = TEXT("raw heightmap must be square 16-bit little endian");
				return false;
			}
			Src.SetNumUninitialized(Count);
			FMemory::Memcpy(Src.GetData(), Bytes.GetData(), Count * 2);
		}
		else
		{
			IImageWrapperModule& Module = FModuleManager::LoadModuleChecked<IImageWrapperModule>("ImageWrapper");
			const EImageFormat Format = Module.DetectImageFormat(Bytes.GetData(), Bytes.Num());
			TSharedPtr<IImageWrapper> Wrapper = Module.CreateImageWrapper(Format);
			TArray<uint8> Raw;
			if (!Wrapper.IsValid() || !Wrapper->SetCompressed(Bytes.GetData(), Bytes.Num()))
			{
				Error = TEXT("unsupported heightmap image (use 8/16-bit grayscale PNG or .r16)");
				return false;
			}
			W = Wrapper->GetWidth();
			H = Wrapper->GetHeight();
			if (Wrapper->GetRaw(ERGBFormat::Gray, 16, Raw))
			{
				Src.SetNumUninitialized(W * H);
				FMemory::Memcpy(Src.GetData(), Raw.GetData(), W * H * 2);
			}
			else if (Wrapper->GetRaw(ERGBFormat::Gray, 8, Raw))
			{
				Src.SetNumUninitialized(W * H);
				for (int32 i = 0; i < W * H; ++i) { Src[i] = static_cast<uint16>(Raw[i]) * 257; }
			}
			else
			{
				Error = TEXT("could not decode heightmap as grayscale");
				return false;
			}
		}
		Out.SetNumUninitialized(SizeX * SizeY);
		for (int32 Y = 0; Y < SizeY; ++Y)
		{
			for (int32 X = 0; X < SizeX; ++X)
			{
				const int32 SX = (W == SizeX) ? X : FMath::Clamp(FMath::RoundToInt(X * float(W - 1) / FMath::Max(1, SizeX - 1)), 0, W - 1);
				const int32 SY = (H == SizeY) ? Y : FMath::Clamp(FMath::RoundToInt(Y * float(H - 1) / FMath::Max(1, SizeY - 1)), 0, H - 1);
				Out[Y * SizeX + X] = Src[SY * W + SX];
			}
		}
		return true;
	}

	/** Resolves a dotted property path to (property, value address). */
	static bool ResolvePath(UObject* Object, const FString& Path, FProperty*& OutProp, void*& OutAddr, FString& Error)
	{
		TArray<FString> Parts;
		Path.ParseIntoArray(Parts, TEXT("."));
		UStruct* Struct = Object->GetClass();
		void* Container = Object;
		for (int32 i = 0; i < Parts.Num(); ++i)
		{
			FProperty* Prop = Struct->FindPropertyByName(FName(*Parts[i]));
			if (!Prop)
			{
				for (TFieldIterator<FProperty> It(Struct); It; ++It)
				{
					if (It->GetName().Equals(Parts[i], ESearchCase::IgnoreCase) ||
						It->GetName().Replace(TEXT("_"), TEXT("")).Equals(Parts[i].Replace(TEXT("_"), TEXT("")), ESearchCase::IgnoreCase))
					{
						Prop = *It;
						break;
					}
				}
			}
			if (!Prop)
			{
				Error = FString::Printf(TEXT("property '%s' not found on %s"), *Parts[i], *Struct->GetName());
				return false;
			}
			void* Addr = Prop->ContainerPtrToValuePtr<void>(Container);
			if (i == Parts.Num() - 1)
			{
				OutProp = Prop;
				OutAddr = Addr;
				return true;
			}
			FStructProperty* StructProp = CastField<FStructProperty>(Prop);
			if (!StructProp)
			{
				Error = FString::Printf(TEXT("'%s' is not a struct; cannot descend"), *Parts[i]);
				return false;
			}
			Struct = StructProp->Struct;
			Container = Addr;
		}
		Error = TEXT("empty property path");
		return false;
	}

	static UWidgetAnimation* FindAnimation(UWidgetBlueprint* WidgetBlueprint, const FString& Name)
	{
		for (UWidgetAnimation* Anim : WidgetBlueprint->Animations)
		{
			if (Anim && Anim->GetName() == Name)
			{
				return Anim;
			}
		}
		return nullptr;
	}
}

using namespace AgentToolkitWorld;

// =================================================================================== EQS

FString UAgentToolkitWorldLibrary::EQSAddOption(UEnvQuery* Query, UClass* GeneratorClass)
{
	if (!Query || !GeneratorClass || !GeneratorClass->IsChildOf(UEnvQueryGenerator::StaticClass()) || GeneratorClass->HasAnyClassFlags(CLASS_Abstract))
	{
		return Fail(TEXT("a concrete EnvQueryGenerator class is required"));
	}
	Touch(Query);
	UEnvQueryOption* Option = NewObject<UEnvQueryOption>(Query, NAME_None, RF_Transactional);
	Option->Generator = NewObject<UEnvQueryGenerator>(Query, GeneratorClass, NAME_None, RF_Transactional);
	Option->Generator->UpdateNodeVersion();
	return FString::FromInt(Query->GetOptionsMutable().Add(Option));
}

FString UAgentToolkitWorldLibrary::EQSAddTest(UEnvQuery* Query, int32 OptionIndex, UClass* TestClass)
{
	if (!Query || !Query->GetOptions().IsValidIndex(OptionIndex))
	{
		return Fail(FString::Printf(TEXT("option %d not found"), OptionIndex));
	}
	if (!TestClass || !TestClass->IsChildOf(UEnvQueryTest::StaticClass()) || TestClass->HasAnyClassFlags(CLASS_Abstract))
	{
		return Fail(TEXT("a concrete EnvQueryTest class is required"));
	}
	Touch(Query);
	UEnvQueryOption* Option = Query->GetOptionsMutable()[OptionIndex];
	UEnvQueryTest* Test = NewObject<UEnvQueryTest>(Query, TestClass, NAME_None, RF_Transactional);
	Test->UpdateNodeVersion();
	Test->TestOrder = Option->Tests.Num();
	return FString::FromInt(Option->Tests.Add(Test));
}

FString UAgentToolkitWorldLibrary::EQSRemove(UEnvQuery* Query, int32 OptionIndex, int32 TestIndex)
{
	if (!Query || !Query->GetOptions().IsValidIndex(OptionIndex))
	{
		return Fail(FString::Printf(TEXT("option %d not found"), OptionIndex));
	}
	UEnvQueryOption* Option = Query->GetOptionsMutable()[OptionIndex];
	if (TestIndex >= 0 && !Option->Tests.IsValidIndex(TestIndex))
	{
		return Fail(FString::Printf(TEXT("test %d not found"), TestIndex));
	}
	Touch(Query);
	if (TestIndex < 0)
	{
		Query->GetOptionsMutable().RemoveAt(OptionIndex);
	}
	else
	{
		Option->Tests.RemoveAt(TestIndex);
	}
	return FString();
}

UObject* UAgentToolkitWorldLibrary::EQSGetNode(UEnvQuery* Query, int32 OptionIndex, int32 TestIndex)
{
	if (!Query || !Query->GetOptions().IsValidIndex(OptionIndex))
	{
		return nullptr;
	}
	UEnvQueryOption* Option = Query->GetOptionsMutable()[OptionIndex];
	if (TestIndex < 0)
	{
		return Option->Generator;
	}
	return Option->Tests.IsValidIndex(TestIndex) ? Option->Tests[TestIndex].Get() : nullptr;
}

FString UAgentToolkitWorldLibrary::EQSDescribe(UEnvQuery* Query)
{
	if (!Query)
	{
		return Fail(TEXT("Query is required"));
	}
	TArray<TSharedPtr<FJsonValue>> Options;
	for (int32 i = 0; i < Query->GetOptions().Num(); ++i)
	{
		const UEnvQueryOption* Option = Query->GetOptions()[i];
		TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
		O->SetNumberField(TEXT("index"), i);
		O->SetStringField(TEXT("generator"), Option && Option->Generator ? Option->Generator->GetClass()->GetName() : TEXT("None"));
		O->SetStringField(TEXT("description"), Option ? Option->GetDescriptionTitle().ToString() : FString());
		TArray<TSharedPtr<FJsonValue>> Tests;
		if (Option)
		{
			for (int32 t = 0; t < Option->Tests.Num(); ++t)
			{
				const UEnvQueryTest* Test = Option->Tests[t];
				TSharedRef<FJsonObject> T = MakeShared<FJsonObject>();
				T->SetNumberField(TEXT("index"), t);
				T->SetStringField(TEXT("class"), Test ? Test->GetClass()->GetName() : TEXT("None"));
				T->SetStringField(TEXT("description"), Test ? Test->GetDescriptionTitle().ToString() + TEXT(" ") + Test->GetDescriptionDetails().ToString() : FString());
				T->SetStringField(TEXT("purpose"), Test ? StaticEnum<EEnvTestPurpose::Type>()->GetNameStringByValue(Test->TestPurpose) : FString());
				Tests.Add(MakeShared<FJsonValueObject>(T));
			}
		}
		O->SetArrayField(TEXT("tests"), Tests);
		Options.Add(MakeShared<FJsonValueObject>(O));
	}
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	Root->SetArrayField(TEXT("options"), Options);
	return ToJson(Root);
}

// ============================================================================= Landscape

ALandscape* UAgentToolkitWorldLibrary::CreateLandscape(FVector Location, FVector Scale, int32 ComponentsX, int32 ComponentsY,
	int32 QuadsPerSection, int32 SectionsPerComponent, const FString& HeightmapFile, UMaterialInterface* Material, FString& OutError)
{
	OutError.Reset();
	static const int32 ValidQuads[] = { 7, 15, 31, 63, 127, 255 };
	bool bValidQuads = false;
	for (int32 Q : ValidQuads) { bValidQuads |= (Q == QuadsPerSection); }
	if (!bValidQuads || (SectionsPerComponent != 1 && SectionsPerComponent != 2) || ComponentsX < 1 || ComponentsY < 1 || ComponentsX * ComponentsY > 1024)
	{
		OutError = TEXT("QuadsPerSection must be 7/15/31/63/127/255, SectionsPerComponent 1 or 2, components 1..32 per side");
		return nullptr;
	}
	UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
	if (!World)
	{
		OutError = TEXT("no editor world");
		return nullptr;
	}
	const int32 QuadsPerComponent = QuadsPerSection * SectionsPerComponent;
	const int32 SizeX = ComponentsX * QuadsPerComponent + 1;
	const int32 SizeY = ComponentsY * QuadsPerComponent + 1;

	TArray<uint16> Heights;
	if (HeightmapFile.IsEmpty())
	{
		Heights.Init(32768, SizeX * SizeY);
	}
	else if (!LoadHeights(HeightmapFile, SizeX, SizeY, Heights, OutError))
	{
		return nullptr;
	}
	TMap<FGuid, TArray<uint16>> HeightDataPerLayers;
	HeightDataPerLayers.Add(FGuid(), MoveTemp(Heights));
	TMap<FGuid, TArray<FLandscapeImportLayerInfo>> MaterialLayerDataPerLayers;
	MaterialLayerDataPerLayers.Add(FGuid(), TArray<FLandscapeImportLayerInfo>());

	const FVector Offset = FTransform(FRotator::ZeroRotator, FVector::ZeroVector, Scale).TransformVector(
		FVector(-ComponentsX * QuadsPerComponent / 2.0, -ComponentsY * QuadsPerComponent / 2.0, 0.0));
	ALandscape* Landscape = World->SpawnActor<ALandscape>(Location + Offset, FRotator::ZeroRotator);
	if (!Landscape)
	{
		OutError = TEXT("could not spawn landscape actor");
		return nullptr;
	}
	Landscape->LandscapeMaterial = Material;
	Landscape->SetActorRelativeScale3D(Scale);
	Landscape->StaticLightingLOD = FMath::DivideAndRoundUp(FMath::CeilLogTwo((SizeX * SizeY) / (2048 * 2048) + 1), (uint32)2);
	Landscape->Import(FGuid::NewGuid(), 0, 0, SizeX - 1, SizeY - 1, SectionsPerComponent, QuadsPerSection, HeightDataPerLayers,
		HeightmapFile.IsEmpty() ? TEXT("") : *HeightmapFile, MaterialLayerDataPerLayers, ELandscapeImportAlphamapType::Additive,
		TArrayView<const FLandscapeLayer>());
	if (ULandscapeInfo* Info = Landscape->GetLandscapeInfo())
	{
		Info->UpdateLayerInfoMap(Landscape);
	}
	FActorLabelUtilities::SetActorLabelUnique(Landscape, TEXT("Landscape"));
	return Landscape;
}

// =================================================================================== UMG

UWidget* UAgentToolkitWorldLibrary::FindWidgetInBlueprint(UWidgetBlueprint* WidgetBlueprint, FName WidgetName)
{
	return (WidgetBlueprint && WidgetBlueprint->WidgetTree) ? WidgetBlueprint->WidgetTree->FindWidget(WidgetName) : nullptr;
}

UWidgetAnimation* UAgentToolkitWorldLibrary::AddWidgetAnimation(UWidgetBlueprint* WidgetBlueprint, const FString& AnimationName, float LengthSeconds)
{
	if (!WidgetBlueprint || AnimationName.IsEmpty() || LengthSeconds <= 0.f)
	{
		return nullptr;
	}
	if (UWidgetAnimation* Existing = FindAnimation(WidgetBlueprint, AnimationName))
	{
		return Existing;
	}
	WidgetBlueprint->Modify();
	UWidgetAnimation* Anim = NewObject<UWidgetAnimation>(WidgetBlueprint, FName(*AnimationName), RF_Transactional);
	Anim->MovieScene = NewObject<UMovieScene>(Anim, FName(*AnimationName), RF_Transactional);
	Anim->MovieScene->SetDisplayRate(FFrameRate(20, 1));
	const FFrameRate TickResolution = Anim->MovieScene->GetTickResolution();
	Anim->MovieScene->SetPlaybackRange(TRange<FFrameNumber>(FFrameNumber(0), (LengthSeconds * TickResolution).CeilToFrame()));
	Anim->MovieScene->GetEditorData().WorkStart = 0.f;
	Anim->MovieScene->GetEditorData().WorkEnd = LengthSeconds;
	WidgetBlueprint->Animations.Add(Anim);
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(WidgetBlueprint);
	return Anim;
}

FString UAgentToolkitWorldLibrary::AddWidgetAnimationKeys(UWidgetBlueprint* WidgetBlueprint, const FString& AnimationName, FName WidgetName,
	FName PropertyName, int32 ChannelIndex, const TArray<float>& Times, const TArray<float>& Values)
{
	if (!WidgetBlueprint)
	{
		return Fail(TEXT("WidgetBlueprint is required"));
	}
	UWidgetAnimation* Anim = FindAnimation(WidgetBlueprint, AnimationName);
	if (!Anim)
	{
		return Fail(FString::Printf(TEXT("animation '%s' not found"), *AnimationName));
	}
	UWidget* Widget = FindWidgetInBlueprint(WidgetBlueprint, WidgetName);
	if (!Widget)
	{
		return Fail(FString::Printf(TEXT("widget '%s' not found"), *WidgetName.ToString()));
	}
	if (Times.Num() == 0 || Times.Num() != Values.Num())
	{
		return Fail(TEXT("Times and Values must be non-empty and the same length"));
	}
	const bool bTransform = PropertyName == FName(TEXT("RenderTransform"));
	FProperty* Property = bTransform ? nullptr : Widget->GetClass()->FindPropertyByName(PropertyName);
	const FStructProperty* StructProp = CastField<FStructProperty>(Property);
	const bool bColor = StructProp && (StructProp->Struct == TBaseStructure<FLinearColor>::Get() || StructProp->Struct->GetFName() == FName(TEXT("SlateColor")));
	if (!bTransform && !bColor && !CastField<FFloatProperty>(Property) && !CastField<FDoubleProperty>(Property))
	{
		return Fail(FString::Printf(TEXT("property '%s' is not a float, LinearColor/SlateColor or RenderTransform on %s"),
			*PropertyName.ToString(), *Widget->GetClass()->GetName()));
	}
	const int32 MaxChannel = bTransform ? 6 : (bColor ? 3 : 0);
	if (ChannelIndex < 0 || ChannelIndex > MaxChannel)
	{
		return Fail(FString::Printf(TEXT("ChannelIndex must be 0..%d for this property"), MaxChannel));
	}

	UMovieScene* MovieScene = Anim->MovieScene;
	Anim->Modify();
	MovieScene->Modify();
	FGuid Guid;
	for (const FWidgetAnimationBinding& Binding : Anim->AnimationBindings)
	{
		if (Binding.WidgetName == WidgetName)
		{
			Guid = Binding.AnimationGuid;
		}
	}
	if (!Guid.IsValid())
	{
		Guid = MovieScene->AddPossessable(WidgetName.ToString(), Widget->GetClass());
		FWidgetAnimationBinding Binding;
		Binding.WidgetName = WidgetName;
		Binding.AnimationGuid = Guid;
		Binding.bIsRootWidget = false;
		Anim->AnimationBindings.Add(Binding);
	}

	UMovieScenePropertyTrack* Track = nullptr;
	for (UMovieSceneTrack* Existing : MovieScene->FindTracks(UMovieScenePropertyTrack::StaticClass(), Guid))
	{
		UMovieScenePropertyTrack* PropTrack = Cast<UMovieScenePropertyTrack>(Existing);
		if (PropTrack && PropTrack->GetPropertyName() == PropertyName)
		{
			Track = PropTrack;
		}
	}
	if (!Track)
	{
		UClass* TrackClass = bTransform ? UMovieScene2DTransformTrack::StaticClass() : (bColor ? UMovieSceneColorTrack::StaticClass() : UMovieSceneFloatTrack::StaticClass());
		Track = Cast<UMovieScenePropertyTrack>(MovieScene->AddTrack(TrackClass, Guid));
		Track->SetPropertyNameAndPath(PropertyName, PropertyName.ToString());
		UMovieSceneSection* NewSection = Track->CreateNewSection();
		NewSection->SetRange(TRange<FFrameNumber>::All());
		Track->AddSection(*NewSection);
	}
	UMovieSceneSection* Section = Track->GetAllSections().Num() ? Track->GetAllSections()[0] : nullptr;
	if (!Section)
	{
		return Fail(TEXT("track has no section"));
	}
	Section->Modify();
	TArrayView<FMovieSceneFloatChannel*> Channels = Section->GetChannelProxy().GetChannels<FMovieSceneFloatChannel>();
	if (!Channels.IsValidIndex(ChannelIndex))
	{
		return Fail(TEXT("channel index out of range for this track"));
	}
	const FFrameRate TickResolution = MovieScene->GetTickResolution();
	for (int32 i = 0; i < Times.Num(); ++i)
	{
		Channels[ChannelIndex]->AddCubicKey((Times[i] * TickResolution).RoundToFrame(), Values[i]);
	}
	FBlueprintEditorUtils::MarkBlueprintAsModified(WidgetBlueprint);
	return Track->GetName();
}

FString UAgentToolkitWorldLibrary::DescribeWidgetAnimations(UWidgetBlueprint* WidgetBlueprint)
{
	if (!WidgetBlueprint)
	{
		return Fail(TEXT("WidgetBlueprint is required"));
	}
	TArray<TSharedPtr<FJsonValue>> Anims;
	for (UWidgetAnimation* Anim : WidgetBlueprint->Animations)
	{
		if (!Anim || !Anim->MovieScene)
		{
			continue;
		}
		TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
		A->SetStringField(TEXT("name"), Anim->GetName());
		A->SetNumberField(TEXT("length"), Anim->GetEndTime() - Anim->GetStartTime());
		TArray<TSharedPtr<FJsonValue>> Bindings;
		for (const FWidgetAnimationBinding& Binding : Anim->AnimationBindings)
		{
			TSharedRef<FJsonObject> B = MakeShared<FJsonObject>();
			B->SetStringField(TEXT("widget"), Binding.WidgetName.ToString());
			TArray<TSharedPtr<FJsonValue>> Tracks;
			for (UMovieSceneTrack* Track : Anim->MovieScene->FindTracks(UMovieSceneTrack::StaticClass(), Binding.AnimationGuid))
			{
				const UMovieScenePropertyTrack* PropTrack = Cast<UMovieScenePropertyTrack>(Track);
				int32 Keys = 0;
				for (UMovieSceneSection* Section : Track->GetAllSections())
				{
					for (FMovieSceneFloatChannel* Channel : Section->GetChannelProxy().GetChannels<FMovieSceneFloatChannel>())
					{
						Keys += Channel->GetNumKeys();
					}
				}
				Tracks.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("%s (%d keys)"),
					PropTrack ? *PropTrack->GetPropertyName().ToString() : *Track->GetClass()->GetName(), Keys)));
			}
			B->SetArrayField(TEXT("tracks"), Tracks);
			Bindings.Add(MakeShared<FJsonValueObject>(B));
		}
		A->SetArrayField(TEXT("bindings"), Bindings);
		Anims.Add(MakeShared<FJsonValueObject>(A));
	}
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	Root->SetArrayField(TEXT("animations"), Anims);
	return ToJson(Root);
}

FString UAgentToolkitWorldLibrary::DescribeLandscape(ALandscapeProxy* Landscape)
{
	if (!Landscape)
	{
		return Fail(TEXT("Landscape is required"));
	}
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	Root->SetStringField(TEXT("label"), Landscape->GetActorLabel());
	Root->SetNumberField(TEXT("components"), Landscape->LandscapeComponents.Num());
	Root->SetNumberField(TEXT("component_size_quads"), Landscape->ComponentSizeQuads);
	Root->SetNumberField(TEXT("subsection_size_quads"), Landscape->SubsectionSizeQuads);
	Root->SetNumberField(TEXT("sections_per_component"), Landscape->NumSubsections);
	if (ULandscapeInfo* Info = Landscape->GetLandscapeInfo())
	{
		// World Partition landscapes keep components in streaming proxies: count through the info.
		Root->SetNumberField(TEXT("components"), Info->XYtoComponentMap.Num());
		FIntRect Extent;
		if (Info->GetLandscapeExtent(Extent))
		{
			Root->SetNumberField(TEXT("size_quads_x"), Extent.Width());
			Root->SetNumberField(TEXT("size_quads_y"), Extent.Height());
		}
	}
	Root->SetStringField(TEXT("material"), Landscape->GetLandscapeMaterial() ? Landscape->GetLandscapeMaterial()->GetPathName() : FString());
	return ToJson(Root);
}

FString UAgentToolkitWorldLibrary::SetPropertyFromText(UObject* Object, const FString& PropertyPath, const FString& Value)
{
	if (!Object)
	{
		return Fail(TEXT("Object is required"));
	}
	FProperty* Prop = nullptr;
	void* Addr = nullptr;
	FString Error;
	if (!ResolvePath(Object, PropertyPath, Prop, Addr, Error))
	{
		return Fail(Error);
	}
	Object->Modify();
	FString Text = Value;
	if (FByteProperty* ByteProp = CastField<FByteProperty>(Prop))
	{
		// Namespaced enums (TEnumAsByte<EFoo::Type>) need the qualified name.
		if (ByteProp->Enum && !Text.Contains(TEXT("::")) && ByteProp->Enum->GetIndexByNameString(Text) == INDEX_NONE)
		{
			const FString Qualified = ByteProp->Enum->GenerateFullEnumName(*Text);
			if (ByteProp->Enum->GetIndexByNameString(Qualified) != INDEX_NONE) { Text = Qualified; }
		}
	}
	if (!Prop->ImportText_Direct(*Text, Addr, Object, PPF_None))
	{
		return Fail(FString::Printf(TEXT("could not parse '%s' for %s (%s)"), *Value, *PropertyPath, *Prop->GetCPPType()));
	}
	FPropertyChangedEvent Event(Prop);
	Object->PostEditChangeProperty(Event);
	Object->MarkPackageDirty();
	return FString();
}

FString UAgentToolkitWorldLibrary::GetPropertyAsText(UObject* Object, const FString& PropertyPath)
{
	if (!Object)
	{
		return Fail(TEXT("Object is required"));
	}
	FProperty* Prop = nullptr;
	void* Addr = nullptr;
	FString Error;
	if (!ResolvePath(Object, PropertyPath, Prop, Addr, Error))
	{
		return Fail(Error);
	}
	FString Out;
	Prop->ExportTextItem_Direct(Out, Addr, nullptr, Object, PPF_None);
	return Out;
}

// ================================================================================= Stats

FString UAgentToolkitWorldLibrary::GetFrameStats()
{
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	const double DeltaMs = FApp::GetDeltaTime() * 1000.0;
	Root->SetNumberField(TEXT("delta_ms"), DeltaMs);
	Root->SetNumberField(TEXT("fps"), DeltaMs > 0.0 ? 1000.0 / DeltaMs : 0.0);
	Root->SetNumberField(TEXT("game_thread_ms"), FPlatformTime::ToMilliseconds(GGameThreadTime));
	Root->SetNumberField(TEXT("render_thread_ms"), FPlatformTime::ToMilliseconds(GRenderThreadTime));
	Root->SetNumberField(TEXT("rhi_thread_ms"), FPlatformTime::ToMilliseconds(GRHIThreadTime));
	Root->SetNumberField(TEXT("gpu_ms"), FPlatformTime::ToMilliseconds(RHIGetGPUFrameCycles(0)));
	Root->SetNumberField(TEXT("draw_calls"), GNumDrawCallsRHI[0]);
	Root->SetNumberField(TEXT("primitives_drawn"), GNumPrimitivesDrawnRHI[0]);
	Root->SetBoolField(TEXT("rendering"), FApp::CanEverRender());
	return ToJson(Root);
}

// =================================================================================== Landscape sculpt / paint

namespace AgentToolkitLandscape
{
	static FString LandscapeFail(const FString& Message) { return TEXT("ERROR: ") + Message; }

	static FString LandscapeJson(const TSharedRef<FJsonObject>& Object)
	{
		FString Text;
		const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text);
		FJsonSerializer::Serialize(Object, Writer);
		return Text;
	}

	/** Brush footprint in landscape vertex coordinates, clamped to the landscape extent. */
	struct FBrush
	{
		ALandscape* Root = nullptr;
		ULandscapeInfo* Info = nullptr;
		FGuid LayerGuid;
		FTransform ToWorld;
		FVector2D LocalCenter = FVector2D::ZeroVector;
		double RadiusQ = 0, InnerQ = 0;
		int32 X1 = 0, Y1 = 0, X2 = -1, Y2 = -1;

		int32 Width() const { return X2 - X1 + 1; }
		int32 Height() const { return Y2 - Y1 + 1; }

		double Weight(int32 X, int32 Y) const
		{
			const double D = FVector2D::Distance(FVector2D(X, Y), LocalCenter);
			if (D > RadiusQ) { return 0.0; }
			if (D <= InnerQ || RadiusQ <= InnerQ) { return 1.0; }
			const double T = (RadiusQ - D) / (RadiusQ - InnerQ);
			return T * T * (3.0 - 2.0 * T);
		}
	};

	static FString MakeBrush(ALandscapeProxy* Proxy, const FVector& Center, float Radius, float Falloff, FBrush& Out)
	{
		if (!Proxy) { return LandscapeFail(TEXT("landscape is null")); }
		Out.Root = Proxy->GetLandscapeActor();
		Out.Info = Proxy->GetLandscapeInfo();
		if (!Out.Root || !Out.Info) { return LandscapeFail(TEXT("landscape has no root actor / info (is it loaded?)")); }
		if (const ULandscapeEditLayerBase* Layer = Out.Root->GetEditLayerConst(0))
		{
			Out.LayerGuid = Layer->GetGuid();
		}
		Out.ToWorld = Out.Root->LandscapeActorToWorld();
		const FVector Local = Out.ToWorld.InverseTransformPosition(Center);
		Out.LocalCenter = FVector2D(Local.X, Local.Y);
		Out.RadiusQ = FMath::Max(0.5, Radius / FMath::Max((double)KINDA_SMALL_NUMBER, Out.ToWorld.GetScale3D().X));
		Out.InnerQ = Out.RadiusQ * (1.0 - FMath::Clamp(Falloff, 0.f, 1.f));
		int32 MinX, MinY, MaxX, MaxY;
		if (!Out.Info->GetLandscapeExtent(MinX, MinY, MaxX, MaxY)) { return LandscapeFail(TEXT("landscape has no components")); }
		Out.X1 = FMath::Max(MinX, FMath::FloorToInt(Local.X - Out.RadiusQ));
		Out.Y1 = FMath::Max(MinY, FMath::FloorToInt(Local.Y - Out.RadiusQ));
		Out.X2 = FMath::Min(MaxX, FMath::CeilToInt(Local.X + Out.RadiusQ));
		Out.Y2 = FMath::Min(MaxY, FMath::CeilToInt(Local.Y + Out.RadiusQ));
		if (Out.X1 > Out.X2 || Out.Y1 > Out.Y2) { return LandscapeFail(TEXT("brush is outside the landscape")); }
		return FString();
	}

	static double TexToWorldZ(const FBrush& B, uint16 Tex)
	{
		return B.ToWorld.TransformPosition(FVector(0, 0, LandscapeDataAccess::GetLocalHeight(Tex))).Z;
	}

	static uint16 WorldZToTex(const FBrush& B, double WorldZ)
	{
		const FVector Origin = B.ToWorld.GetLocation();
		const FVector Local = B.ToWorld.InverseTransformPosition(FVector(Origin.X, Origin.Y, WorldZ));
		return (uint16)FMath::Clamp<int32>(FMath::RoundToInt(Local.Z / LANDSCAPE_ZSCALE + LandscapeDataAccess::MidValue), 0, 65535);
	}
}

FString UAgentToolkitWorldLibrary::LandscapeSculpt(ALandscapeProxy* Landscape, FVector Center, float Radius, float Falloff,
	const FString& Mode, float Strength, float TargetHeight)
{
	using namespace AgentToolkitLandscape;
	const FString M = Mode.ToLower();
	if (M != TEXT("raise") && M != TEXT("lower") && M != TEXT("flatten") && M != TEXT("smooth"))
	{
		return LandscapeFail(TEXT("mode must be raise, lower, flatten or smooth"));
	}
	FBrush B;
	const FString Error = MakeBrush(Landscape, Center, Radius, Falloff, B);
	if (!Error.IsEmpty()) { return Error; }

	const int32 W = B.Width(), H = B.Height();
	TArray<uint16> Data;
	Data.SetNumZeroed(W * H);
	int32 Vertices = 0;
	double MinZ = TNumericLimits<double>::Max(), MaxZ = TNumericLimits<double>::Lowest();
	{
		ALandscape* Root = B.Root;
		FScopedSetLandscapeEditingLayer Scope(Root, B.LayerGuid, [Root] { Root->RequestLayersContentUpdateForceAll(); });
		FLandscapeEditDataInterface Edit(B.Info);
		Edit.GetHeightDataFast(B.X1, B.Y1, B.X2, B.Y2, Data.GetData(), W);
		const TArray<uint16> Original = Data;
		const double ScaleZ = FMath::Max((double)KINDA_SMALL_NUMBER, B.ToWorld.GetScale3D().Z);
		const double DeltaTex = Strength / ScaleZ / LANDSCAPE_ZSCALE;
		const double Target = WorldZToTex(B, TargetHeight);
		const double Blend = FMath::Clamp(Strength, 0.f, 1.f);
		for (int32 Y = 0; Y < H; ++Y)
		{
			for (int32 X = 0; X < W; ++X)
			{
				const double Wt = B.Weight(B.X1 + X, B.Y1 + Y);
				if (Wt <= 0.0) { continue; }
				const double Old = Original[Y * W + X];
				double New = Old;
				if (M == TEXT("raise")) { New = Old + Wt * DeltaTex; }
				else if (M == TEXT("lower")) { New = Old - Wt * DeltaTex; }
				else if (M == TEXT("flatten")) { New = FMath::Lerp(Old, Target, Wt * Blend); }
				else
				{
					double Sum = 0;
					int32 Count = 0;
					for (int32 DY = -1; DY <= 1; ++DY)
					{
						for (int32 DX = -1; DX <= 1; ++DX)
						{
							const int32 NX = X + DX, NY = Y + DY;
							if (NX >= 0 && NX < W && NY >= 0 && NY < H) { Sum += Original[NY * W + NX]; ++Count; }
						}
					}
					New = FMath::Lerp(Old, Sum / Count, Wt * Blend);
				}
				const uint16 Value = (uint16)FMath::Clamp<int32>(FMath::RoundToInt(New), 0, 65535);
				Data[Y * W + X] = Value;
				++Vertices;
				const double Z = TexToWorldZ(B, Value);
				MinZ = FMath::Min(MinZ, Z);
				MaxZ = FMath::Max(MaxZ, Z);
			}
		}
		Root->Modify();
		Edit.SetHeightData(B.X1, B.Y1, B.X2, B.Y2, Data.GetData(), W, true);
	}
	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetNumberField(TEXT("vertices"), Vertices);
	Out->SetStringField(TEXT("region"), FString::Printf(TEXT("%d,%d,%d,%d"), B.X1, B.Y1, B.X2, B.Y2));
	if (Vertices > 0)
	{
		Out->SetNumberField(TEXT("min_z"), MinZ);
		Out->SetNumberField(TEXT("max_z"), MaxZ);
	}
	return LandscapeJson(Out);
}

FString UAgentToolkitWorldLibrary::LandscapePaintLayer(ALandscapeProxy* Landscape, FName LayerName, FVector Center, float Radius,
	float Falloff, float Strength, const FString& LayerInfoFolder)
{
	using namespace AgentToolkitLandscape;
	if (LayerName.IsNone()) { return LandscapeFail(TEXT("layer name is required")); }
	FBrush B;
	const FString Error = MakeBrush(Landscape, Center, Radius, Falloff, B);
	if (!Error.IsEmpty()) { return Error; }

	bool bCreated = false;
	ULandscapeLayerInfoObject* LayerInfo = B.Info->GetLayerInfoByName(LayerName);
	if (!LayerInfo)
	{
		const FString Folder = LayerInfoFolder.IsEmpty() ? FString(TEXT("/Game/Landscape/LayerInfos")) : LayerInfoFolder;
		LayerInfo = UE::Landscape::CreateTargetLayerInfo(LayerName, Folder);
		if (!LayerInfo) { return LandscapeFail(FString::Printf(TEXT("could not create a Layer Info asset in %s"), *Folder)); }
		bCreated = true;
	}
	ALandscape* Root = B.Root;
	if (!Root->HasTargetLayer(LayerInfo))
	{
		Root->Modify();
		if (Root->HasTargetLayer(LayerName))
		{
			Root->UpdateTargetLayer(LayerName, FLandscapeTargetLayerSettings(LayerInfo));
		}
		else
		{
			Root->AddTargetLayer(LayerName, FLandscapeTargetLayerSettings(LayerInfo));
		}
		B.Info->UpdateLayerInfoMap(Root);
	}

	const int32 W = B.Width(), H = B.Height();
	TArray<uint8> Data;
	Data.SetNumZeroed(W * H);
	int32 Vertices = 0;
	{
		FScopedSetLandscapeEditingLayer Scope(Root, B.LayerGuid, [Root] { Root->RequestLayersContentUpdateForceAll(); });
		FLandscapeEditDataInterface Edit(B.Info);
		Edit.GetWeightDataFast(LayerInfo, B.X1, B.Y1, B.X2, B.Y2, Data.GetData(), W);
		const double Amount = FMath::Clamp(Strength, -1.f, 1.f) * 255.0;
		for (int32 Y = 0; Y < H; ++Y)
		{
			for (int32 X = 0; X < W; ++X)
			{
				const double Wt = B.Weight(B.X1 + X, B.Y1 + Y);
				if (Wt <= 0.0) { continue; }
				uint8& V = Data[Y * W + X];
				V = (uint8)FMath::Clamp<int32>(FMath::RoundToInt(V + Wt * Amount), 0, 255);
				++Vertices;
			}
		}
		Edit.SetAlphaData(LayerInfo, B.X1, B.Y1, B.X2, B.Y2, Data.GetData(), W);
	}
	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetNumberField(TEXT("vertices"), Vertices);
	Out->SetStringField(TEXT("layer_info"), LayerInfo->GetPathName());
	Out->SetBoolField(TEXT("created_layer_info"), bCreated);
	return LandscapeJson(Out);
}

FString UAgentToolkitWorldLibrary::LandscapeSample(ALandscapeProxy* Landscape, FVector Location, FName LayerName)
{
	using namespace AgentToolkitLandscape;
	FBrush B;
	const FString Error = MakeBrush(Landscape, Location, 0.5f, 0.f, B);
	if (!Error.IsEmpty()) { return Error; }
	const int32 X = FMath::Clamp(FMath::RoundToInt(B.LocalCenter.X), B.X1, B.X2);
	const int32 Y = FMath::Clamp(FMath::RoundToInt(B.LocalCenter.Y), B.Y1, B.Y2);
	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	FScopedSetLandscapeEditingLayer Scope(B.Root, B.LayerGuid);
	FLandscapeEditDataInterface Edit(B.Info);
	uint16 Height = 0;
	Edit.GetHeightDataFast(X, Y, X, Y, &Height, 1);
	Out->SetNumberField(TEXT("z"), TexToWorldZ(B, Height));
	Out->SetStringField(TEXT("vertex"), FString::Printf(TEXT("%d,%d"), X, Y));
	if (!LayerName.IsNone())
	{
		ULandscapeLayerInfoObject* LayerInfo = B.Info->GetLayerInfoByName(LayerName);
		if (!LayerInfo) { return LandscapeFail(FString::Printf(TEXT("layer %s not found"), *LayerName.ToString())); }
		uint8 Weight = 0;
		Edit.GetWeightDataFast(LayerInfo, X, Y, X, Y, &Weight, 1);
		Out->SetNumberField(TEXT("weight"), Weight / 255.0);
	}
	return LandscapeJson(Out);
}

FString UAgentToolkitWorldLibrary::LandscapeListTargetLayers(ALandscapeProxy* Landscape)
{
	TArray<TSharedPtr<FJsonValue>> Layers;
	if (Landscape)
	{
		const ALandscapeProxy* Source = Landscape->GetLandscapeActor() ? static_cast<ALandscapeProxy*>(Landscape->GetLandscapeActor()) : Landscape;
		for (const TPair<FName, FLandscapeTargetLayerSettings>& Pair : Source->GetTargetLayers())
		{
			TSharedRef<FJsonObject> L = MakeShared<FJsonObject>();
			L->SetStringField(TEXT("name"), Pair.Key.ToString());
			L->SetStringField(TEXT("layer_info"), Pair.Value.LayerInfoObj ? Pair.Value.LayerInfoObj->GetPathName() : FString());
			Layers.Add(MakeShared<FJsonValueObject>(L));
		}
	}
	FString Text;
	const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
		TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text);
	FJsonSerializer::Serialize(Layers, Writer);
	return Text;
}
