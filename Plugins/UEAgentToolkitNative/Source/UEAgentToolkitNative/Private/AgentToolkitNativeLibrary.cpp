#include "AgentToolkitNativeLibrary.h"

#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "IMessageLogListing.h"
#include "K2Node_CustomEvent.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/CompilerResultsLog.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Logging/TokenizedMessage.h"
#include "MessageLogModule.h"
#include "Modules/ModuleManager.h"

namespace AgentToolkitNative
{
	static const TCHAR* SeverityName(EMessageSeverity::Type Severity)
	{
		switch (Severity)
		{
		case EMessageSeverity::Error: return TEXT("Error");
		case EMessageSeverity::PerformanceWarning: return TEXT("PerformanceWarning");
		case EMessageSeverity::Warning: return TEXT("Warning");
		default: return TEXT("Info");
		}
	}

	static TArray<FString> ToStrings(const TArray<TSharedRef<FTokenizedMessage>>& Messages)
	{
		TArray<FString> Out;
		Out.Reserve(Messages.Num());
		for (const TSharedRef<FTokenizedMessage>& Message : Messages)
		{
			Out.Add(FString::Printf(TEXT("%s: %s"), SeverityName(Message->GetSeverity()), *Message->ToText().ToString()));
		}
		return Out;
	}

	// Well-known listings; Message Log has no public enumeration API.
	static const TCHAR* KnownLogs[] = {
		TEXT("BlueprintLog"), TEXT("PIE"), TEXT("MapCheck"), TEXT("AssetCheck"), TEXT("LoadErrors"),
		TEXT("EditorErrors"), TEXT("AssetTools"), TEXT("PackagingResults"), TEXT("SourceControl"),
		TEXT("AutomationTestingLog"), TEXT("LightingResults"), TEXT("HLODResults"), TEXT("SlateStyleLog"),
		TEXT("AnimBlueprintLog"), TEXT("UDNParser"), TEXT("TranslationEditor"), TEXT("LocalizationService"),
		TEXT("Niagara"), TEXT("WorldPartition"), TEXT("DataValidation")
	};
}

bool UAgentToolkitNativeLibrary::AddInterfaceToBlueprint(UBlueprint* Blueprint, UClass* InterfaceClass)
{
	if (!Blueprint || !InterfaceClass || !InterfaceClass->HasAnyClassFlags(CLASS_Interface))
	{
		return false;
	}
	return FBlueprintEditorUtils::ImplementNewInterface(Blueprint, InterfaceClass->GetClassPathName());
}

bool UAgentToolkitNativeLibrary::RemoveInterfaceFromBlueprint(UBlueprint* Blueprint, UClass* InterfaceClass, bool bPreserveFunctions)
{
	if (!Blueprint || !InterfaceClass)
	{
		return false;
	}
	const bool bWasImplemented = GetImplementedInterfaces(Blueprint).Contains(InterfaceClass);
	if (bWasImplemented)
	{
		FBlueprintEditorUtils::RemoveInterface(Blueprint, InterfaceClass->GetClassPathName(), bPreserveFunctions);
	}
	return bWasImplemented;
}

TArray<UClass*> UAgentToolkitNativeLibrary::GetImplementedInterfaces(UBlueprint* Blueprint)
{
	TArray<UClass*> Out;
	if (Blueprint)
	{
		for (const FBPInterfaceDescription& Desc : Blueprint->ImplementedInterfaces)
		{
			if (Desc.Interface)
			{
				Out.Add(Desc.Interface.Get());
			}
		}
	}
	return Out;
}

UEdGraph* UAgentToolkitNativeLibrary::AddMacroGraph(UBlueprint* Blueprint, const FString& MacroName)
{
	if (!Blueprint || MacroName.IsEmpty())
	{
		return nullptr;
	}
	const FName GraphName(*MacroName);
	TArray<UEdGraph*> AllGraphs;
	Blueprint->GetAllGraphs(AllGraphs);
	for (const UEdGraph* Existing : AllGraphs)
	{
		if (Existing && Existing->GetFName() == GraphName)
		{
			return nullptr;
		}
	}
	UEdGraph* NewGraph = FBlueprintEditorUtils::CreateNewGraph(Blueprint, GraphName, UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
	FBlueprintEditorUtils::AddMacroGraph(Blueprint, NewGraph, /*bIsUserCreated*/ true, /*SignatureFromClass*/ nullptr);
	return NewGraph;
}

bool UAgentToolkitNativeLibrary::SetCustomEventReplication(UK2Node_CustomEvent* CustomEvent, int32 NetMode, bool bReliable)
{
	if (!CustomEvent || NetMode < 0 || NetMode > 3)
	{
		return false;
	}
	CustomEvent->Modify();
	CustomEvent->FunctionFlags &= ~(FUNC_Net | FUNC_NetServer | FUNC_NetClient | FUNC_NetMulticast | FUNC_NetReliable);
	if (NetMode > 0)
	{
		const uint32 ModeFlag = NetMode == 1 ? FUNC_NetServer : (NetMode == 2 ? FUNC_NetClient : FUNC_NetMulticast);
		CustomEvent->FunctionFlags |= FUNC_Net | ModeFlag;
		if (bReliable)
		{
			CustomEvent->FunctionFlags |= FUNC_NetReliable;
		}
	}
	if (UBlueprint* Blueprint = FBlueprintEditorUtils::FindBlueprintForNode(CustomEvent))
	{
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
	}
	return true;
}

bool UAgentToolkitNativeLibrary::AddCustomEventInput(UK2Node_CustomEvent* CustomEvent, FName InputName, const FEdGraphPinType& PinType)
{
	if (!CustomEvent || InputName.IsNone() || CustomEvent->FindPin(InputName))
	{
		return false;
	}
	CustomEvent->Modify();
	UEdGraphPin* Pin = CustomEvent->CreateUserDefinedPin(InputName, PinType, EGPD_Output);
	if (!Pin)
	{
		return false;
	}
	if (UBlueprint* Blueprint = FBlueprintEditorUtils::FindBlueprintForNode(CustomEvent))
	{
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
	}
	return true;
}

FString UAgentToolkitNativeLibrary::GetCustomEventReplication(UK2Node_CustomEvent* CustomEvent)
{
	if (!CustomEvent)
	{
		return FString();
	}
	const uint32 Flags = CustomEvent->FunctionFlags;
	FString Mode = TEXT("None");
	if (Flags & FUNC_Net)
	{
		Mode = (Flags & FUNC_NetServer) ? TEXT("Server") : (Flags & FUNC_NetClient) ? TEXT("Client") : TEXT("Multicast");
		if (Flags & FUNC_NetReliable)
		{
			Mode += TEXT(",Reliable");
		}
	}
	return Mode;
}

bool UAgentToolkitNativeLibrary::SetVariableReplicationCondition(UBlueprint* Blueprint, FName VariableName, int32 Condition)
{
	if (!Blueprint || Condition < 0 || Condition >= COND_Max)
	{
		return false;
	}
	const int32 Index = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VariableName);
	if (Index == INDEX_NONE)
	{
		return false;
	}
	Blueprint->Modify();
	Blueprint->NewVariables[Index].ReplicationCondition = static_cast<ELifetimeCondition>(Condition);
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
	return true;
}

int32 UAgentToolkitNativeLibrary::GetVariableReplicationCondition(UBlueprint* Blueprint, FName VariableName)
{
	if (!Blueprint)
	{
		return -1;
	}
	const int32 Index = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VariableName);
	return Index == INDEX_NONE ? -1 : static_cast<int32>(Blueprint->NewVariables[Index].ReplicationCondition.GetValue());
}

TArray<FString> UAgentToolkitNativeLibrary::CompileBlueprintWithLog(UBlueprint* Blueprint)
{
	if (!Blueprint)
	{
		return {};
	}
	FCompilerResultsLog Results;
	Results.SetSourcePath(Blueprint->GetPathName());
	Results.bSilentMode = true;
	FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection, &Results);
	return AgentToolkitNative::ToStrings(Results.Messages);
}

TArray<FString> UAgentToolkitNativeLibrary::GetMessageLogMessages(FName LogName)
{
	FMessageLogModule& MessageLog = FModuleManager::LoadModuleChecked<FMessageLogModule>("MessageLog");
	if (!MessageLog.IsRegisteredLogListing(LogName))
	{
		return {};
	}
	return AgentToolkitNative::ToStrings(MessageLog.GetLogListing(LogName)->GetFilteredMessages());
}

TArray<FString> UAgentToolkitNativeLibrary::GetKnownMessageLogNames()
{
	FMessageLogModule& MessageLog = FModuleManager::LoadModuleChecked<FMessageLogModule>("MessageLog");
	TArray<FString> Out;
	for (const TCHAR* Name : AgentToolkitNative::KnownLogs)
	{
		if (MessageLog.IsRegisteredLogListing(Name))
		{
			Out.Add(Name);
		}
	}
	return Out;
}
