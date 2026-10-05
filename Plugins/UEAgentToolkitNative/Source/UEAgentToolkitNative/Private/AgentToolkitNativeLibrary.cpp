#include "AgentToolkitNativeLibrary.h"

#include "EdGraphSchema_K2.h"
#include "EdGraph/EdGraph.h"
#include "K2Node_AddPinInterface.h"
#include "K2Node_CallFunction.h"
#include "K2Node_CreateDelegate.h"
#include "K2Node_EditablePinBase.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_FunctionResult.h"
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

// ---------------------------------------------------------------- node pins / signatures
namespace AgentToolkitNative
{
	static FString Error(const FString& Message)
	{
		return TEXT("ERROR: ") + Message;
	}

	static void MarkModified(UEdGraphNode* Node)
	{
		if (UBlueprint* Blueprint = FBlueprintEditorUtils::FindBlueprintForNode(Node))
		{
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		}
	}

	/** Function graph or event dispatcher signature graph by name. */
	static UEdGraph* FindSignatureGraph(UBlueprint* Blueprint, FName GraphName)
	{
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			if (Graph && Graph->GetFName() == GraphName)
			{
				return Graph;
			}
		}
		for (UEdGraph* Graph : Blueprint->DelegateSignatureGraphs)
		{
			if (Graph && Graph->GetFName() == GraphName)
			{
				return Graph;
			}
		}
		return nullptr;
	}

	/** Editable-pin nodes carrying the parameters: the entry node for inputs, result nodes for function outputs. */
	static TArray<UK2Node_EditablePinBase*> FindParamNodes(UEdGraph* Graph, bool bOutput)
	{
		TArray<UK2Node_EditablePinBase*> Result;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (!bOutput && Node && Node->IsA<UK2Node_FunctionEntry>())
			{
				Result.Add(CastChecked<UK2Node_EditablePinBase>(Node));
			}
			else if (bOutput && Node && Node->IsA<UK2Node_FunctionResult>())
			{
				Result.Add(CastChecked<UK2Node_EditablePinBase>(Node));
			}
		}
		return Result;
	}
}

FString UAgentToolkitNativeLibrary::AddNodePin(UEdGraphNode* Node)
{
	using namespace AgentToolkitNative;
	if (!Node)
	{
		return Error(TEXT("Node is null"));
	}
	IK2Node_AddPinInterface* AddPin = Cast<IK2Node_AddPinInterface>(Node);
	if (!AddPin)
	{
		return Error(FString::Printf(TEXT("%s does not support adding pins"), *Node->GetClass()->GetName()));
	}
	if (!AddPin->CanAddPin())
	{
		return Error(TEXT("No more pins can be added to this node"));
	}
	TSet<FName> Before;
	for (UEdGraphPin* Pin : Node->Pins)
	{
		Before.Add(Pin->PinName);
	}
	Node->Modify();
	AddPin->AddInputPin();
	MarkModified(Node);
	for (UEdGraphPin* Pin : Node->Pins)
	{
		if (!Before.Contains(Pin->PinName))
		{
			return Pin->PinName.ToString();
		}
	}
	return FString();
}

FString UAgentToolkitNativeLibrary::RemoveNodePin(UEdGraphNode* Node, FName PinName)
{
	using namespace AgentToolkitNative;
	if (!Node)
	{
		return Error(TEXT("Node is null"));
	}
	IK2Node_AddPinInterface* AddPin = Cast<IK2Node_AddPinInterface>(Node);
	if (!AddPin)
	{
		return Error(FString::Printf(TEXT("%s does not support removing pins"), *Node->GetClass()->GetName()));
	}
	UEdGraphPin* Pin = Node->FindPin(PinName);
	if (!Pin)
	{
		return Error(FString::Printf(TEXT("Pin %s not found"), *PinName.ToString()));
	}
	if (!AddPin->CanRemovePin(Pin))
	{
		return Error(FString::Printf(TEXT("Pin %s cannot be removed"), *PinName.ToString()));
	}
	Node->Modify();
	AddPin->RemoveInputPin(Pin);
	MarkModified(Node);
	return FString();
}

FString UAgentToolkitNativeLibrary::RetargetCallFunctionClass(UK2Node_CallFunction* Node, UClass* NewClass)
{
	using namespace AgentToolkitNative;
	if (!Node || !NewClass)
	{
		return Error(TEXT("Node and class are required"));
	}
	const FName FunctionName = Node->FunctionReference.GetMemberName();
	UFunction* Function = NewClass->FindFunctionByName(FunctionName);
	if (!Function)
	{
		return Error(FString::Printf(TEXT("Function %s not found on %s"), *FunctionName.ToString(), *NewClass->GetName()));
	}
	Node->Modify();
	Node->SetFromFunction(Function);
	Node->ReconstructNode();
	MarkModified(Node);
	return FString();
}

FString UAgentToolkitNativeLibrary::AddEventDispatcher(UBlueprint* Blueprint, FName DispatcherName)
{
	using namespace AgentToolkitNative;
	if (!Blueprint || DispatcherName.IsNone())
	{
		return Error(TEXT("Blueprint and a dispatcher name are required"));
	}
	if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, DispatcherName) != INDEX_NONE || FindSignatureGraph(Blueprint, DispatcherName))
	{
		return Error(FString::Printf(TEXT("%s already exists"), *DispatcherName.ToString()));
	}
	Blueprint->Modify();
	FEdGraphPinType DelegateType;
	DelegateType.PinCategory = UEdGraphSchema_K2::PC_MCDelegate;
	if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, DispatcherName, DelegateType))
	{
		return Error(TEXT("Could not add the delegate variable"));
	}
	UEdGraph* Graph = FBlueprintEditorUtils::CreateNewGraph(Blueprint, DispatcherName, UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
	if (!Graph)
	{
		return Error(TEXT("Could not create the signature graph"));
	}
	Graph->bEditable = false;
	const UEdGraphSchema_K2* K2Schema = GetDefault<UEdGraphSchema_K2>();
	K2Schema->CreateDefaultNodesForGraph(*Graph);
	K2Schema->CreateFunctionGraphTerminators(*Graph, static_cast<UClass*>(nullptr));
	K2Schema->AddExtraFunctionFlags(Graph, (FUNC_BlueprintCallable | FUNC_BlueprintEvent | FUNC_Public));
	K2Schema->MarkFunctionEntryAsEditable(Graph, true);
	Blueprint->DelegateSignatureGraphs.Add(Graph);
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
	return FString();
}

FString UAgentToolkitNativeLibrary::AddGraphParam(UBlueprint* Blueprint, FName GraphName, FName ParamName, const FEdGraphPinType& PinType, bool bOutput)
{
	using namespace AgentToolkitNative;
	UEdGraph* Graph = Blueprint ? FindSignatureGraph(Blueprint, GraphName) : nullptr;
	if (!Graph)
	{
		return Error(FString::Printf(TEXT("Function or dispatcher %s not found"), *GraphName.ToString()));
	}
	const TArray<UK2Node_EditablePinBase*> Nodes = FindParamNodes(Graph, bOutput);
	if (Nodes.IsEmpty())
	{
		return Error(bOutput ? TEXT("Graph has no output (Return) node; add a return node or an output via create_blueprint_function")
							 : TEXT("Graph has no entry node"));
	}
	for (UK2Node_EditablePinBase* Node : Nodes)
	{
		if (Node->FindPin(ParamName))
		{
			return Error(FString::Printf(TEXT("Parameter %s already exists"), *ParamName.ToString()));
		}
	}
	// Entry nodes expose inputs as output pins; result nodes take outputs as input pins.
	const EEdGraphPinDirection Direction = bOutput ? EGPD_Input : EGPD_Output;
	for (UK2Node_EditablePinBase* Node : Nodes)
	{
		Node->Modify();
		if (!Node->CreateUserDefinedPin(ParamName, PinType, Direction))
		{
			return Error(FString::Printf(TEXT("Could not add parameter %s"), *ParamName.ToString()));
		}
	}
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
	return FString();
}

FString UAgentToolkitNativeLibrary::RemoveGraphParam(UBlueprint* Blueprint, FName GraphName, FName ParamName, bool bOutput)
{
	using namespace AgentToolkitNative;
	UEdGraph* Graph = Blueprint ? FindSignatureGraph(Blueprint, GraphName) : nullptr;
	if (!Graph)
	{
		return Error(FString::Printf(TEXT("Function or dispatcher %s not found"), *GraphName.ToString()));
	}
	bool bRemoved = false;
	for (UK2Node_EditablePinBase* Node : FindParamNodes(Graph, bOutput))
	{
		if (Node->FindPin(ParamName))
		{
			Node->Modify();
			Node->RemoveUserDefinedPinByName(ParamName);
			bRemoved = true;
		}
	}
	if (!bRemoved)
	{
		return Error(FString::Printf(TEXT("Parameter %s not found"), *ParamName.ToString()));
	}
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
	return FString();
}

FName UAgentToolkitNativeLibrary::GetCreateEventFunction(UK2Node_CreateDelegate* Node)
{
	return Node ? Node->GetFunctionName() : NAME_None;
}

FString UAgentToolkitNativeLibrary::SetCreateEventFunction(UK2Node_CreateDelegate* Node, FName FunctionName)
{
	using namespace AgentToolkitNative;
	if (!Node)
	{
		return Error(TEXT("Node is null"));
	}
	Node->Modify();
	Node->SetFunction(FunctionName);
	Node->HandleAnyChange(true);
	if (Node->GetFunctionName() != FunctionName)
	{
		return Error(FString::Printf(TEXT("Function %s is not compatible with this delegate"), *FunctionName.ToString()));
	}
	MarkModified(Node);
	return FString();
}

TArray<FString> UAgentToolkitNativeLibrary::ListCompatibleEventFunctions(UK2Node_CreateDelegate* Node)
{
	TArray<FString> Result;
	if (!Node)
	{
		return Result;
	}
	const UFunction* Signature = Node->GetDelegateSignature();
	UClass* ScopeClass = Node->GetScopeClass();
	if (!Signature || !ScopeClass)
	{
		return Result;
	}
	auto ParamsOf = [](const UFunction* Function)
	{
		TArray<const FProperty*> Params;
		for (TFieldIterator<FProperty> It(Function); It; ++It)
		{
			if (It->HasAnyPropertyFlags(CPF_Parm) && !It->HasAnyPropertyFlags(CPF_ReturnParm))
			{
				Params.Add(*It);
			}
		}
		return Params;
	};
	const TArray<const FProperty*> Wanted = ParamsOf(Signature);
	for (TFieldIterator<UFunction> It(ScopeClass); It; ++It)
	{
		const UFunction* Candidate = *It;
		if (Candidate->HasAnyFunctionFlags(FUNC_Delegate) || Candidate->GetName().Contains(TEXT("__")))
		{
			continue;
		}
		const TArray<const FProperty*> Params = ParamsOf(Candidate);
		bool bMatches = Params.Num() == Wanted.Num();
		for (int32 Index = 0; bMatches && Index < Params.Num(); ++Index)
		{
			bMatches = Params[Index]->SameType(Wanted[Index]);
		}
		if (bMatches)
		{
			Result.Add(Candidate->GetName());
		}
	}
	return Result;
}
