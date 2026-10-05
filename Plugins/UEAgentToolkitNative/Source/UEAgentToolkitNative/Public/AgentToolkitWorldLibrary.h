#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "AgentToolkitWorldLibrary.generated.h"

class ALandscape;
class ALandscapeProxy;
class UEnvQuery;
class UMaterialInterface;
class UWidget;
class UWidgetAnimation;
class UWidgetBlueprint;

/**
 * Helpers for EQS, Landscape, UMG widget animations and runtime frame statistics.
 * Exposed to Python as unreal.AgentToolkitWorldLibrary. FString results are empty (or a
 * created name/index) on success and "ERROR: <reason>" on failure.
 */
UCLASS()
class UEAGENTTOOLKITNATIVE_API UAgentToolkitWorldLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// --------------------------------------------------------------------- EQS
	/** Adds an option (generator) to an Environment Query. Returns the option index. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|EQS")
	static FString EQSAddOption(UEnvQuery* Query, UClass* GeneratorClass);

	/** Adds a test (filter/score) to an option. Returns the test index. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|EQS")
	static FString EQSAddTest(UEnvQuery* Query, int32 OptionIndex, UClass* TestClass);

	/** Removes an option (TestIndex < 0) or a single test. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|EQS")
	static FString EQSRemove(UEnvQuery* Query, int32 OptionIndex, int32 TestIndex);

	/** Generator (TestIndex < 0) or test object, for setting properties from Python. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|EQS")
	static UObject* EQSGetNode(UEnvQuery* Query, int32 OptionIndex, int32 TestIndex);

	/** JSON description of options, generators and tests. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|EQS")
	static FString EQSDescribe(UEnvQuery* Query);

	// --------------------------------------------------------------- Landscape
	/**
	 * Creates a landscape in the editor world. Size = ComponentsX/Y * SectionsPerComponent * QuadsPerSection quads.
	 * HeightmapFile: optional 16-bit .r16/.raw (little endian, (quads+1)^2 samples) or grayscale .png (resampled
	 * if the size differs). Empty = flat.
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Landscape")
	static ALandscape* CreateLandscape(FVector Location, FVector Scale, int32 ComponentsX, int32 ComponentsY,
		int32 QuadsPerSection, int32 SectionsPerComponent, const FString& HeightmapFile, UMaterialInterface* Material,
		FString& OutError);

	// --------------------------------------------------------------------- UMG
	/** Finds a widget by name in a Widget Blueprint's designer tree (use its Slot for layout). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|UMG")
	static UWidget* FindWidgetInBlueprint(UWidgetBlueprint* WidgetBlueprint, FName WidgetName);

	/** Creates (or returns) a widget animation with the given length in seconds. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|UMG")
	static UWidgetAnimation* AddWidgetAnimation(UWidgetBlueprint* WidgetBlueprint, const FString& AnimationName, float LengthSeconds);

	/**
	 * Adds keys for a widget property. PropertyName: a float property (RenderOpacity), "RenderTransform"
	 * (ChannelIndex 0/1 translation X/Y, 2 angle, 3/4 scale X/Y, 5/6 shear X/Y) or a LinearColor/SlateColor
	 * property such as ColorAndOpacity (ChannelIndex 0..3 = R,G,B,A).
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|UMG")
	static FString AddWidgetAnimationKeys(UWidgetBlueprint* WidgetBlueprint, const FString& AnimationName, FName WidgetName,
		FName PropertyName, int32 ChannelIndex, const TArray<float>& Times, const TArray<float>& Values);

	/** JSON: animations with length, bound widgets and tracks. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|UMG")
	static FString DescribeWidgetAnimations(UWidgetBlueprint* WidgetBlueprint);

	/** JSON: component count, size in quads, sections, component size, material. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Landscape")
	static FString DescribeLandscape(ALandscapeProxy* Landscape);

	// --------------------------------------------------------------- Properties
	/**
	 * Sets any reflected property from Unreal text format, with dotted paths into structs,
	 * e.g. ("TestPurpose", "Score"), ("SearchRadius.DefaultValue", "3000"), ("Color", "(R=1,G=0,B=0,A=1)").
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Properties")
	static FString SetPropertyFromText(UObject* Object, const FString& PropertyPath, const FString& Value);

	/** Reads any reflected property (dotted path) as Unreal text. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Properties")
	static FString GetPropertyAsText(UObject* Object, const FString& PropertyPath);

	// ------------------------------------------------------------------- Stats
	/** JSON of last-frame timings (game/render/RHI/GPU ms), delta time, draw calls and primitives. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Performance")
	static FString GetFrameStats();
};
