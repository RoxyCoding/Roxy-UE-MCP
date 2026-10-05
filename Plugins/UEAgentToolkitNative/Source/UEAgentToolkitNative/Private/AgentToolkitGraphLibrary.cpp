#include "AgentToolkitGraphLibrary.h"

#include "BehaviorTree/Tasks/BTTask_RunBehavior.h"

#include "AIGraphTypes.h"
#include "AssetToolsModule.h"
#include "Animation/AnimInstance.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Bool.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Class.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Enum.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Float.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Int.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Name.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Object.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Rotator.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_String.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Vector.h"
#include "Factories/AnimBlueprintFactory.h"
#include "Factories/SoundCueFactoryNew.h"
#include "Sound/SoundCue.h"
#include "Sound/SoundNodeWavePlayer.h"
#include "Sound/SoundWave.h"
#include "AnimGraphNode_BlendSpacePlayer.h"
#include "AnimGraphNode_Root.h"
#include "AnimGraphNode_SequencePlayer.h"
#include "AnimGraphNode_StateMachine.h"
#include "AnimGraphNode_StateResult.h"
#include "AnimGraphNode_TransitionResult.h"
#include "AnimStateEntryNode.h"
#include "AnimStateNode.h"
#include "AnimStateTransitionNode.h"
#include "Animation/AnimBlueprint.h"
#include "Animation/AnimMontage.h"
#include "Animation/AnimSequence.h"
#include "Animation/BlendSpace.h"
#include "AnimationStateGraph.h"
#include "AnimationStateMachineGraph.h"
#include "AnimationTransitionGraph.h"
#include "BehaviorTree/BTCompositeNode.h"
#include "BehaviorTree/BTDecorator.h"
#include "BehaviorTree/BTService.h"
#include "BehaviorTree/BTTaskNode.h"
#include "BehaviorTree/BehaviorTree.h"
#include "BehaviorTree/BlackboardData.h"
#include "BehaviorTree/Composites/BTComposite_SimpleParallel.h"
#include "BehaviorTreeGraph.h"
#include "BehaviorTreeGraphNode_Composite.h"
#include "BehaviorTreeGraphNode_Decorator.h"
#include "BehaviorTreeGraphNode_Root.h"
#include "BehaviorTreeGraphNode_Service.h"
#include "BehaviorTreeGraphNode_Task.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraphSchema_BehaviorTree.h"
#include "K2Node_CallFunction.h"
#include "K2Node_VariableGet.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace AgentToolkitGraph
{
	static FString Fail(const FString& Message) { return TEXT("ERROR: ") + Message; }

	static FString ToJson(const TSharedRef<FJsonObject>& Object)
	{
		FString Out;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Object, Writer);
		return Out;
	}

	static UEdGraphPin* FirstPin(UEdGraphNode* Node, EEdGraphPinDirection Direction)
	{
		if (Node)
		{
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin && Pin->Direction == Direction && !Pin->bHidden)
				{
					return Pin;
				}
			}
		}
		return nullptr;
	}

	static UEdGraphPin* NthPin(UEdGraphNode* Node, EEdGraphPinDirection Direction, int32 Index)
	{
		int32 Count = 0;
		if (Node)
		{
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin && Pin->Direction == Direction && !Pin->bHidden && Count++ == Index)
				{
					return Pin;
				}
			}
		}
		return nullptr;
	}

	static bool Connect(UEdGraph* Graph, UEdGraphPin* A, UEdGraphPin* B)
	{
		return Graph && A && B && Graph->GetSchema()->TryCreateConnection(A, B);
	}

	// ---------------------------------------------------------------- Behavior Tree helpers
	static UBehaviorTreeGraph* EnsureBTGraph(UBehaviorTree* BehaviorTree)
	{
		if (!BehaviorTree)
		{
			return nullptr;
		}
		UBehaviorTreeGraph* Graph = Cast<UBehaviorTreeGraph>(BehaviorTree->BTGraph);
		if (!Graph)
		{
			BehaviorTree->Modify();
			Graph = CastChecked<UBehaviorTreeGraph>(FBlueprintEditorUtils::CreateNewGraph(
				BehaviorTree, TEXT("Behavior Tree"), UBehaviorTreeGraph::StaticClass(), UEdGraphSchema_BehaviorTree::StaticClass()));
			BehaviorTree->BTGraph = Graph;
			Graph->GetSchema()->CreateDefaultNodesForGraph(*Graph);
			Graph->OnCreated();
		}
		return Graph;
	}

	static UBehaviorTreeGraphNode* FindBTNode(UBehaviorTreeGraph* Graph, const FString& Name)
	{
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			UBehaviorTreeGraphNode* BTNode = Cast<UBehaviorTreeGraphNode>(Node);
			if (!BTNode)
			{
				continue;
			}
			if (Name.IsEmpty() || Name.Equals(TEXT("Root"), ESearchCase::IgnoreCase))
			{
				if (BTNode->IsA<UBehaviorTreeGraphNode_Root>())
				{
					return BTNode;
				}
				continue;
			}
			if (BTNode->GetName() == Name)
			{
				return BTNode;
			}
			for (UAIGraphNode* Sub : BTNode->SubNodes)
			{
				if (Sub && Sub->GetName() == Name)
				{
					return Cast<UBehaviorTreeGraphNode>(Sub);
				}
			}
		}
		return nullptr;
	}

	static TSharedRef<FJsonObject> DescribeBTNode(UBehaviorTreeGraphNode* Node)
	{
		TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("id"), Node->GetName());
		Obj->SetStringField(TEXT("graph_class"), Node->GetClass()->GetName());
		Obj->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::ListView).ToString());
		if (Node->NodeInstance)
		{
			Obj->SetStringField(TEXT("class"), Node->NodeInstance->GetClass()->GetPathName());
		}
		TArray<TSharedPtr<FJsonValue>> Decorators, Services, Children;
		for (UBehaviorTreeGraphNode* Sub : Node->Decorators)
		{
			if (Sub) { Decorators.Add(MakeShared<FJsonValueObject>(DescribeBTNode(Sub))); }
		}
		for (UBehaviorTreeGraphNode* Sub : Node->Services)
		{
			if (Sub) { Services.Add(MakeShared<FJsonValueObject>(DescribeBTNode(Sub))); }
		}
		int32 OutputCount = 0;
		for (UEdGraphPin* Pin : Node->Pins) { OutputCount += (Pin && Pin->Direction == EGPD_Output && !Pin->bHidden) ? 1 : 0; }
		for (int32 OutIndex = 0; OutIndex < OutputCount; ++OutIndex)
		{
			UEdGraphPin* Out = NthPin(Node, EGPD_Output, OutIndex);
			TArray<UEdGraphPin*> Links = Out->LinkedTo;
			Links.Sort([](const UEdGraphPin& A, const UEdGraphPin& B) { return A.GetOwningNode()->NodePosX < B.GetOwningNode()->NodePosX; });
			for (UEdGraphPin* Link : Links)
			{
				if (UBehaviorTreeGraphNode* Child = Cast<UBehaviorTreeGraphNode>(Link->GetOwningNode()))
				{
					TSharedRef<FJsonObject> ChildObj = DescribeBTNode(Child);
					if (OutputCount > 1)
					{
						ChildObj->SetStringField(TEXT("branch"), OutIndex == 0 ? TEXT("main") : TEXT("background"));
					}
					Children.Add(MakeShared<FJsonValueObject>(ChildObj));
				}
			}
		}
		if (Decorators.Num()) { Obj->SetArrayField(TEXT("decorators"), Decorators); }
		if (Services.Num()) { Obj->SetArrayField(TEXT("services"), Services); }
		if (Children.Num()) { Obj->SetArrayField(TEXT("children"), Children); }
		return Obj;
	}

	// ---------------------------------------------------------------- Animation helpers
	static UEdGraph* FindAnimGraph(UAnimBlueprint* AnimBlueprint)
	{
		for (UEdGraph* Graph : AnimBlueprint->FunctionGraphs)
		{
			if (Graph && Graph->GetFName() == FName(TEXT("AnimGraph")))
			{
				return Graph;
			}
		}
		return nullptr;
	}

	static UAnimGraphNode_StateMachineBase* FindStateMachine(UAnimBlueprint* AnimBlueprint, const FString& Name)
	{
		TArray<UAnimGraphNode_StateMachineBase*> Machines;
		FBlueprintEditorUtils::GetAllNodesOfClass<UAnimGraphNode_StateMachineBase>(AnimBlueprint, Machines);
		for (UAnimGraphNode_StateMachineBase* Machine : Machines)
		{
			if (Machine && Machine->EditorStateMachineGraph && Machine->EditorStateMachineGraph->GetName() == Name)
			{
				return Machine;
			}
		}
		return nullptr;
	}

	static UAnimStateNodeBase* FindState(UAnimationStateMachineGraph* Graph, const FString& Name)
	{
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			UAnimStateNodeBase* State = Cast<UAnimStateNodeBase>(Node);
			if (State && !State->IsA<UAnimStateTransitionNode>() && State->GetStateName() == Name)
			{
				return State;
			}
		}
		return nullptr;
	}

	static bool HasVariable(UBlueprint* Blueprint, FName Name)
	{
		if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, Name) != INDEX_NONE)
		{
			return true;
		}
		UClass* Search = Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass.Get() : Blueprint->GeneratedClass.Get();
		return Search && Search->FindPropertyByName(Name) != nullptr;
	}

	static UK2Node_VariableGet* AddVariableGet(UEdGraph* Graph, FName Variable, int32 X, int32 Y)
	{
		FGraphNodeCreator<UK2Node_VariableGet> Creator(*Graph);
		UK2Node_VariableGet* Get = Creator.CreateNode();
		Get->VariableReference.SetSelfMember(Variable);
		Get->NodePosX = X;
		Get->NodePosY = Y;
		Creator.Finalize();
		return Get;
	}

	static FString RuleSummary(UAnimStateTransitionNode* Transition)
	{
		if (Transition->bAutomaticRuleBasedOnSequencePlayerInState)
		{
			return TEXT("auto");
		}
		UAnimationTransitionGraph* RuleGraph = Cast<UAnimationTransitionGraph>(Transition->BoundGraph);
		UAnimGraphNode_TransitionResult* Result = RuleGraph ? RuleGraph->GetResultNode() : nullptr;
		UEdGraphPin* CanEnter = Result ? Result->FindPin(TEXT("bCanEnterTransition")) : nullptr;
		if (!CanEnter)
		{
			return TEXT("unknown");
		}
		if (CanEnter->LinkedTo.Num() == 0)
		{
			return CanEnter->DefaultValue.Equals(TEXT("true"), ESearchCase::IgnoreCase) ? TEXT("always") : TEXT("never");
		}
		UEdGraphNode* Source = CanEnter->LinkedTo[0]->GetOwningNode();
		if (UK2Node_VariableGet* Get = Cast<UK2Node_VariableGet>(Source))
		{
			return TEXT("bool:") + Get->GetVarName().ToString();
		}
		if (UK2Node_CallFunction* Call = Cast<UK2Node_CallFunction>(Source))
		{
			for (UEdGraphPin* Pin : Call->Pins)
			{
				if (Pin->Direction == EGPD_Input && Pin->LinkedTo.Num())
				{
					if (UK2Node_VariableGet* Get = Cast<UK2Node_VariableGet>(Pin->LinkedTo[0]->GetOwningNode()))
					{
						return Call->GetFunctionName().ToString() + TEXT(":") + Get->GetVarName().ToString();
					}
				}
			}
			return TEXT("function:") + Call->GetFunctionName().ToString();
		}
		return TEXT("custom:") + Source->GetNodeTitle(ENodeTitleType::ListView).ToString();
	}
}

using namespace AgentToolkitGraph;

// ============================================================================= Behavior Tree

FString UAgentToolkitGraphLibrary::BTAddNode(UBehaviorTree* BehaviorTree, const FString& ParentNodeName, UClass* NodeClass, int32 ParentOutputIndex)
{
	UBehaviorTreeGraph* Graph = EnsureBTGraph(BehaviorTree);
	if (!Graph || !NodeClass)
	{
		return Fail(TEXT("BehaviorTree and NodeClass are required"));
	}
	UBehaviorTreeGraphNode* Parent = FindBTNode(Graph, ParentNodeName);
	if (!Parent)
	{
		return Fail(FString::Printf(TEXT("parent node '%s' not found"), *ParentNodeName));
	}
	if (Parent->IsA<UBehaviorTreeGraphNode_Task>() || Parent->IsSubNode())
	{
		return Fail(TEXT("parent must be the root or a composite node"));
	}
	const bool bComposite = NodeClass->IsChildOf(UBTCompositeNode::StaticClass());
	const bool bTask = NodeClass->IsChildOf(UBTTaskNode::StaticClass());
	if ((!bComposite && !bTask) || NodeClass->HasAnyClassFlags(CLASS_Abstract))
	{
		return Fail(FString::Printf(TEXT("%s is not a concrete composite or task class"), *NodeClass->GetName()));
	}
	const bool bSimpleParallel = NodeClass->IsChildOf(UBTComposite_SimpleParallel::StaticClass());
	UEdGraphPin* ParentOut = NthPin(Parent, EGPD_Output, ParentOutputIndex);
	if (!ParentOut)
	{
		return Fail(FString::Printf(TEXT("parent has no output %d"), ParentOutputIndex));
	}
	if (Parent->GetClass()->GetName() == TEXT("BehaviorTreeGraphNode_SimpleParallel") && ParentOutputIndex == 0
		&& (!bTask || ParentOut->LinkedTo.Num() > 0))
	{
		return Fail(TEXT("Simple Parallel main task (output 0) must be exactly one task; use output 1 for the background branch"));
	}
	const bool bIsRoot = Parent->IsA<UBehaviorTreeGraphNode_Root>();
	if (bIsRoot && (!bComposite || (ParentOut && ParentOut->LinkedTo.Num() > 0)))
	{
		return Fail(TEXT("the root accepts exactly one child and it must be a composite (Selector/Sequence)"));
	}

	Graph->Modify();
	UBehaviorTreeGraphNode* NewNode = nullptr;
	// Run Behavior (subtree) tasks use a dedicated graph node, like the editor's schema does.
	const bool bSubtree = NodeClass->IsChildOf(UBTTask_RunBehavior::StaticClass());
	if (bSimpleParallel || bSubtree)
	{
		// These graph node classes are not exported: create them through reflection.
		const TCHAR* GraphNodeClassPath = bSubtree ? TEXT("/Script/BehaviorTreeEditor.BehaviorTreeGraphNode_SubtreeTask")
												   : TEXT("/Script/BehaviorTreeEditor.BehaviorTreeGraphNode_SimpleParallel");
		UClass* GraphNodeClass = FindObject<UClass>(nullptr, GraphNodeClassPath);
		if (!GraphNodeClass)
		{
			return Fail(FString::Printf(TEXT("%s class not found"), GraphNodeClassPath));
		}
		UBehaviorTreeGraphNode* Node = NewObject<UBehaviorTreeGraphNode>(Graph, GraphNodeClass, NAME_None, RF_Transactional);
		Node->ClassData = FGraphNodeClassData(NodeClass, FString());
		Graph->AddNode(Node, false, false);
		Node->CreateNewGuid();
		Node->PostPlacedNewNode();
		if (Node->Pins.Num() == 0)
		{
			Node->AllocateDefaultPins();
		}
		NewNode = Node;
	}
	else if (bComposite)
	{
		FGraphNodeCreator<UBehaviorTreeGraphNode_Composite> Creator(*Graph);
		UBehaviorTreeGraphNode_Composite* Node = Creator.CreateNode();
		Node->ClassData = FGraphNodeClassData(NodeClass, FString());
		Creator.Finalize();
		NewNode = Node;
	}
	else
	{
		FGraphNodeCreator<UBehaviorTreeGraphNode_Task> Creator(*Graph);
		UBehaviorTreeGraphNode_Task* Node = Creator.CreateNode();
		Node->ClassData = FGraphNodeClassData(NodeClass, FString());
		Creator.Finalize();
		NewNode = Node;
	}
	const int32 SiblingCount = ParentOut->LinkedTo.Num() + ParentOutputIndex * 2;
	NewNode->NodePosX = Parent->NodePosX + SiblingCount * 320;
	NewNode->NodePosY = Parent->NodePosY + 220;
	if (!Connect(Graph, ParentOut, FirstPin(NewNode, EGPD_Input)))
	{
		Graph->RemoveNode(NewNode);
		return Fail(TEXT("schema rejected the connection to the parent"));
	}
	Graph->UpdateAsset();
	BehaviorTree->MarkPackageDirty();
	return NewNode->GetName();
}

FString UAgentToolkitGraphLibrary::BTAddSubNode(UBehaviorTree* BehaviorTree, const FString& OwnerNodeName, UClass* SubNodeClass)
{
	UBehaviorTreeGraph* Graph = EnsureBTGraph(BehaviorTree);
	if (!Graph || !SubNodeClass)
	{
		return Fail(TEXT("BehaviorTree and SubNodeClass are required"));
	}
	UBehaviorTreeGraphNode* Owner = FindBTNode(Graph, OwnerNodeName);
	if (!Owner || Owner->IsA<UBehaviorTreeGraphNode_Root>() || Owner->IsSubNode())
	{
		return Fail(FString::Printf(TEXT("'%s' is not a composite/task node of this tree"), *OwnerNodeName));
	}
	UBehaviorTreeGraphNode* Sub = nullptr;
	if (SubNodeClass->IsChildOf(UBTDecorator::StaticClass()))
	{
		Sub = NewObject<UBehaviorTreeGraphNode_Decorator>(Graph);
	}
	else if (SubNodeClass->IsChildOf(UBTService::StaticClass()))
	{
		if (Owner->IsA<UBehaviorTreeGraphNode_Task>() == false && Owner->IsA<UBehaviorTreeGraphNode_Composite>() == false)
		{
			return Fail(TEXT("services can only be added to composites or tasks"));
		}
		Sub = NewObject<UBehaviorTreeGraphNode_Service>(Graph);
	}
	if (!Sub || SubNodeClass->HasAnyClassFlags(CLASS_Abstract))
	{
		return Fail(FString::Printf(TEXT("%s is not a concrete decorator or service class"), *SubNodeClass->GetName()));
	}
	Sub->ClassData = FGraphNodeClassData(SubNodeClass, FString());
	Owner->AddSubNode(Sub, Graph);
	Graph->UpdateAsset();
	BehaviorTree->MarkPackageDirty();
	return Sub->GetName();
}

FString UAgentToolkitGraphLibrary::BTRemoveNode(UBehaviorTree* BehaviorTree, const FString& NodeName)
{
	UBehaviorTreeGraph* Graph = EnsureBTGraph(BehaviorTree);
	UBehaviorTreeGraphNode* Node = Graph ? FindBTNode(Graph, NodeName) : nullptr;
	if (!Node || Node->IsA<UBehaviorTreeGraphNode_Root>())
	{
		return Fail(FString::Printf(TEXT("node '%s' not found (the root cannot be removed)"), *NodeName));
	}
	Graph->Modify();
	if (Node->IsSubNode() && Node->ParentNode)
	{
		Node->ParentNode->RemoveSubNode(Node);
	}
	else
	{
		Node->BreakAllNodeLinks();
		Graph->RemoveNode(Node);
	}
	Graph->UpdateAsset();
	BehaviorTree->MarkPackageDirty();
	return FString();
}

UObject* UAgentToolkitGraphLibrary::BTGetNodeInstance(UBehaviorTree* BehaviorTree, const FString& NodeName)
{
	UBehaviorTreeGraph* Graph = EnsureBTGraph(BehaviorTree);
	UBehaviorTreeGraphNode* Node = Graph ? FindBTNode(Graph, NodeName) : nullptr;
	return Node ? Node->NodeInstance.Get() : nullptr;
}

FString UAgentToolkitGraphLibrary::BTSetBlackboardKey(UBehaviorTree* BehaviorTree, const FString& NodeName, FName PropertyName, FName KeyName)
{
	UObject* Instance = BTGetNodeInstance(BehaviorTree, NodeName);
	if (!Instance)
	{
		return Fail(FString::Printf(TEXT("node '%s' not found"), *NodeName));
	}
	if (!BehaviorTree->BlackboardAsset)
	{
		return Fail(TEXT("the Behavior Tree has no Blackboard asset"));
	}
	FStructProperty* Prop = FindFProperty<FStructProperty>(Instance->GetClass(), PropertyName);
	if (!Prop || Prop->Struct != FBlackboardKeySelector::StaticStruct())
	{
		return Fail(FString::Printf(TEXT("%s has no FBlackboardKeySelector property '%s'"), *Instance->GetClass()->GetName(), *PropertyName.ToString()));
	}
	if (BehaviorTree->BlackboardAsset->GetKeyID(KeyName) == FBlackboard::InvalidKey)
	{
		return Fail(FString::Printf(TEXT("blackboard has no key '%s'"), *KeyName.ToString()));
	}
	Instance->Modify();
	FBlackboardKeySelector* Selector = Prop->ContainerPtrToValuePtr<FBlackboardKeySelector>(Instance);
	Selector->SelectedKeyName = KeyName;
	Selector->ResolveSelectedKey(*BehaviorTree->BlackboardAsset);
	if (Selector->IsSet() == false)
	{
		return Fail(FString::Printf(TEXT("key '%s' type is not allowed by this selector"), *KeyName.ToString()));
	}
	return BTUpdateAsset(BehaviorTree);
}

FString UAgentToolkitGraphLibrary::BTUpdateAsset(UBehaviorTree* BehaviorTree)
{
	UBehaviorTreeGraph* Graph = EnsureBTGraph(BehaviorTree);
	if (!Graph)
	{
		return Fail(TEXT("invalid Behavior Tree"));
	}
	Graph->UpdateAsset();
	BehaviorTree->MarkPackageDirty();
	return FString();
}

FString UAgentToolkitGraphLibrary::BTDescribeGraph(UBehaviorTree* BehaviorTree)
{
	UBehaviorTreeGraph* Graph = EnsureBTGraph(BehaviorTree);
	if (!Graph)
	{
		return Fail(TEXT("invalid Behavior Tree"));
	}
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	if (UBehaviorTreeGraphNode* RootNode = FindBTNode(Graph, TEXT("Root")))
	{
		Root->SetObjectField(TEXT("root"), DescribeBTNode(RootNode));
	}
	TArray<TSharedPtr<FJsonValue>> Orphans;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		UBehaviorTreeGraphNode* BTNode = Cast<UBehaviorTreeGraphNode>(Node);
		UEdGraphPin* In = BTNode ? FirstPin(BTNode, EGPD_Input) : nullptr;
		if (BTNode && !BTNode->IsA<UBehaviorTreeGraphNode_Root>() && In && In->LinkedTo.Num() == 0)
		{
			Orphans.Add(MakeShared<FJsonValueString>(BTNode->GetName()));
		}
	}
	Root->SetArrayField(TEXT("unconnected_nodes"), Orphans);
	return ToJson(Root);
}

FString UAgentToolkitGraphLibrary::BBAddKey(UBlackboardData* Blackboard, FName KeyName, const FString& KeyType, UObject* BaseClassOrEnum, bool bInstanceSynced)
{
	if (!Blackboard || KeyName.IsNone())
	{
		return Fail(TEXT("Blackboard and KeyName are required"));
	}
	if (Blackboard->GetKeyID(KeyName) != FBlackboard::InvalidKey)
	{
		return Fail(FString::Printf(TEXT("key '%s' already exists (possibly inherited)"), *KeyName.ToString()));
	}
	const FString Type = KeyType.ToLower();
	UBlackboardKeyType* Key = nullptr;
	if (Type == TEXT("bool")) { Key = NewObject<UBlackboardKeyType_Bool>(Blackboard); }
	else if (Type == TEXT("int")) { Key = NewObject<UBlackboardKeyType_Int>(Blackboard); }
	else if (Type == TEXT("float")) { Key = NewObject<UBlackboardKeyType_Float>(Blackboard); }
	else if (Type == TEXT("string")) { Key = NewObject<UBlackboardKeyType_String>(Blackboard); }
	else if (Type == TEXT("name")) { Key = NewObject<UBlackboardKeyType_Name>(Blackboard); }
	else if (Type == TEXT("vector")) { Key = NewObject<UBlackboardKeyType_Vector>(Blackboard); }
	else if (Type == TEXT("rotator")) { Key = NewObject<UBlackboardKeyType_Rotator>(Blackboard); }
	else if (Type == TEXT("object"))
	{
		UBlackboardKeyType_Object* ObjectKey = NewObject<UBlackboardKeyType_Object>(Blackboard);
		if (UClass* BaseClass = Cast<UClass>(BaseClassOrEnum)) { ObjectKey->BaseClass = BaseClass; }
		Key = ObjectKey;
	}
	else if (Type == TEXT("class"))
	{
		UBlackboardKeyType_Class* ClassKey = NewObject<UBlackboardKeyType_Class>(Blackboard);
		if (UClass* BaseClass = Cast<UClass>(BaseClassOrEnum)) { ClassKey->BaseClass = BaseClass; }
		Key = ClassKey;
	}
	else if (Type == TEXT("enum"))
	{
		UEnum* Enum = Cast<UEnum>(BaseClassOrEnum);
		if (!Enum) { return Fail(TEXT("enum keys need an enum object")); }
		UBlackboardKeyType_Enum* EnumKey = NewObject<UBlackboardKeyType_Enum>(Blackboard);
		EnumKey->EnumType = Enum;
		Key = EnumKey;
	}
	if (!Key)
	{
		return Fail(TEXT("KeyType must be bool, int, float, string, name, vector, rotator, object, class or enum"));
	}
	Blackboard->Modify();
	FBlackboardEntry Entry;
	Entry.EntryName = KeyName;
	Entry.KeyType = Key;
	Entry.bInstanceSynced = bInstanceSynced;
	Blackboard->Keys.Add(Entry);
	Blackboard->MarkPackageDirty();
	return FString();
}

FString UAgentToolkitGraphLibrary::BBRemoveKey(UBlackboardData* Blackboard, FName KeyName)
{
	if (!Blackboard)
	{
		return Fail(TEXT("Blackboard is required"));
	}
	const int32 Index = Blackboard->Keys.IndexOfByPredicate([&](const FBlackboardEntry& E) { return E.EntryName == KeyName; });
	if (Index == INDEX_NONE)
	{
		return Fail(FString::Printf(TEXT("key '%s' is not declared in this blackboard"), *KeyName.ToString()));
	}
	Blackboard->Modify();
	Blackboard->Keys.RemoveAt(Index);
	Blackboard->MarkPackageDirty();
	return FString();
}

FString UAgentToolkitGraphLibrary::BBDescribeKeys(UBlackboardData* Blackboard)
{
	if (!Blackboard)
	{
		return Fail(TEXT("Blackboard is required"));
	}
	TArray<TSharedPtr<FJsonValue>> Keys;
	auto AddKeys = [&Keys](const TArray<FBlackboardEntry>& Entries, bool bInherited)
	{
		for (const FBlackboardEntry& Entry : Entries)
		{
			TSharedRef<FJsonObject> K = MakeShared<FJsonObject>();
			K->SetStringField(TEXT("name"), Entry.EntryName.ToString());
			K->SetStringField(TEXT("type"), Entry.KeyType ? Entry.KeyType->GetClass()->GetName().Replace(TEXT("BlackboardKeyType_"), TEXT("")) : TEXT("None"));
			if (const UBlackboardKeyType_Object* O = Cast<UBlackboardKeyType_Object>(Entry.KeyType)) { K->SetStringField(TEXT("base_class"), O->BaseClass ? O->BaseClass->GetPathName() : TEXT("")); }
			if (const UBlackboardKeyType_Class* C = Cast<UBlackboardKeyType_Class>(Entry.KeyType)) { K->SetStringField(TEXT("base_class"), C->BaseClass ? C->BaseClass->GetPathName() : TEXT("")); }
			if (const UBlackboardKeyType_Enum* E = Cast<UBlackboardKeyType_Enum>(Entry.KeyType)) { K->SetStringField(TEXT("enum"), E->EnumType ? E->EnumType->GetPathName() : TEXT("")); }
			K->SetBoolField(TEXT("instance_synced"), Entry.bInstanceSynced);
			K->SetBoolField(TEXT("inherited"), bInherited);
			Keys.Add(MakeShared<FJsonValueObject>(K));
		}
	};
	AddKeys(Blackboard->ParentKeys, true);
	AddKeys(Blackboard->Keys, false);
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	Root->SetStringField(TEXT("parent"), Blackboard->Parent ? Blackboard->Parent->GetPathName() : FString());
	Root->SetArrayField(TEXT("keys"), Keys);
	return ToJson(Root);
}

// ============================================================================= Animation

UAnimBlueprint* UAgentToolkitGraphLibrary::CreateAnimBlueprint(const FString& FolderPath, const FString& AssetName, USkeleton* Skeleton, UClass* ParentClass)
{
	if (!Skeleton || AssetName.IsEmpty())
	{
		return nullptr;
	}
	UAnimBlueprintFactory* Factory = NewObject<UAnimBlueprintFactory>();
	Factory->TargetSkeleton = Skeleton;
	Factory->ParentClass = (ParentClass && ParentClass->IsChildOf(UAnimInstance::StaticClass())) ? ParentClass : UAnimInstance::StaticClass();
	IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools").Get();
	return Cast<UAnimBlueprint>(AssetTools.CreateAsset(AssetName, FolderPath, UAnimBlueprint::StaticClass(), Factory));
}

FString UAgentToolkitGraphLibrary::AnimAddStateMachine(UAnimBlueprint* AnimBlueprint, const FString& MachineName, bool bConnectToOutputPose)
{
	if (!AnimBlueprint || MachineName.IsEmpty())
	{
		return Fail(TEXT("AnimBlueprint and MachineName are required"));
	}
	UEdGraph* AnimGraph = FindAnimGraph(AnimBlueprint);
	if (!AnimGraph)
	{
		return Fail(TEXT("AnimGraph not found (template or interface Anim Blueprint?)"));
	}
	if (FindStateMachine(AnimBlueprint, MachineName))
	{
		return Fail(FString::Printf(TEXT("state machine '%s' already exists"), *MachineName));
	}
	AnimGraph->Modify();
	FGraphNodeCreator<UAnimGraphNode_StateMachine> Creator(*AnimGraph);
	UAnimGraphNode_StateMachine* Machine = Creator.CreateNode();
	Machine->NodePosX = -400;
	Machine->NodePosY = 0;
	Creator.Finalize();
	if (!Machine->EditorStateMachineGraph)
	{
		return Fail(TEXT("state machine graph was not created"));
	}
	FBlueprintEditorUtils::RenameGraph(Machine->EditorStateMachineGraph, MachineName);
	if (bConnectToOutputPose)
	{
		for (UEdGraphNode* Node : AnimGraph->Nodes)
		{
			if (UAnimGraphNode_Root* Root = Cast<UAnimGraphNode_Root>(Node))
			{
				UEdGraphPin* In = FirstPin(Root, EGPD_Input);
				if (In)
				{
					In->BreakAllPinLinks();
					Connect(AnimGraph, FirstPin(Machine, EGPD_Output), In);
				}
				break;
			}
		}
	}
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(AnimBlueprint);
	return Machine->EditorStateMachineGraph->GetName();
}

FString UAgentToolkitGraphLibrary::AnimAddState(UAnimBlueprint* AnimBlueprint, const FString& MachineName, const FString& StateName,
	UAnimationAsset* AnimationAsset, FName BlendSpaceXVariable, FName BlendSpaceYVariable, bool bSetAsEntryState)
{
	UAnimGraphNode_StateMachineBase* Machine = AnimBlueprint ? FindStateMachine(AnimBlueprint, MachineName) : nullptr;
	if (!Machine)
	{
		return Fail(FString::Printf(TEXT("state machine '%s' not found"), *MachineName));
	}
	UAnimationStateMachineGraph* MachineGraph = Machine->EditorStateMachineGraph;
	if (StateName.IsEmpty() || FindState(MachineGraph, StateName))
	{
		return Fail(FString::Printf(TEXT("state name '%s' is empty or already used"), *StateName));
	}
	if (AnimationAsset && AnimBlueprint->TargetSkeleton && AnimationAsset->GetSkeleton() &&
		!AnimBlueprint->TargetSkeleton->IsCompatibleForEditor(AnimationAsset->GetSkeleton()))
	{
		return Fail(TEXT("animation asset uses a skeleton incompatible with the Anim Blueprint"));
	}
	for (const FName& Var : { BlendSpaceXVariable, BlendSpaceYVariable })
	{
		if (!Var.IsNone() && !HasVariable(AnimBlueprint, Var))
		{
			return Fail(FString::Printf(TEXT("variable '%s' does not exist on the Anim Blueprint"), *Var.ToString()));
		}
	}

	MachineGraph->Modify();
	int32 StateCount = 0;
	for (UEdGraphNode* Node : MachineGraph->Nodes)
	{
		StateCount += Node->IsA<UAnimStateNode>() ? 1 : 0;
	}
	FGraphNodeCreator<UAnimStateNode> Creator(*MachineGraph);
	UAnimStateNode* State = Creator.CreateNode();
	State->NodePosX = 300 + (StateCount % 4) * 300;
	State->NodePosY = (StateCount / 4) * 250;
	Creator.Finalize();
	State->OnRenameNode(StateName);

	if (AnimationAsset)
	{
		UAnimationStateGraph* StateGraph = Cast<UAnimationStateGraph>(State->BoundGraph);
		UAnimGraphNode_StateResult* Result = StateGraph ? StateGraph->GetResultNode() : nullptr;
		if (!Result)
		{
			return Fail(TEXT("state graph has no result node"));
		}
		UAnimGraphNode_Base* Player = nullptr;
		if (UBlendSpace* BlendSpace = Cast<UBlendSpace>(AnimationAsset))
		{
			FGraphNodeCreator<UAnimGraphNode_BlendSpacePlayer> PlayerCreator(*StateGraph);
			UAnimGraphNode_BlendSpacePlayer* BSPlayer = PlayerCreator.CreateNode();
			BSPlayer->SetAnimationAsset(BlendSpace);
			BSPlayer->NodePosX = Result->NodePosX - 350;
			BSPlayer->NodePosY = Result->NodePosY;
			PlayerCreator.Finalize();
			Player = BSPlayer;
			const FName Axes[2] = { BlendSpaceXVariable, BlendSpaceYVariable };
			const TCHAR* PinNames[2] = { TEXT("X"), TEXT("Y") };
			for (int32 Axis = 0; Axis < 2; ++Axis)
			{
				if (!Axes[Axis].IsNone())
				{
					UK2Node_VariableGet* Get = AddVariableGet(StateGraph, Axes[Axis], BSPlayer->NodePosX - 250, BSPlayer->NodePosY + Axis * 80);
					Connect(StateGraph, Get->FindPin(Axes[Axis]), BSPlayer->FindPin(PinNames[Axis]));
				}
			}
		}
		else
		{
			FGraphNodeCreator<UAnimGraphNode_SequencePlayer> PlayerCreator(*StateGraph);
			UAnimGraphNode_SequencePlayer* SeqPlayer = PlayerCreator.CreateNode();
			SeqPlayer->SetAnimationAsset(AnimationAsset);
			SeqPlayer->NodePosX = Result->NodePosX - 350;
			SeqPlayer->NodePosY = Result->NodePosY;
			PlayerCreator.Finalize();
			Player = SeqPlayer;
		}
		if (!Connect(StateGraph, FirstPin(Player, EGPD_Output), FirstPin(Result, EGPD_Input)))
		{
			return Fail(TEXT("could not connect the animation player to the state result"));
		}
	}

	UEdGraphPin* EntryOut = MachineGraph->EntryNode ? MachineGraph->EntryNode->GetOutputPin() : nullptr;
	if (EntryOut && (bSetAsEntryState || EntryOut->LinkedTo.Num() == 0))
	{
		EntryOut->BreakAllPinLinks();
		EntryOut->MakeLinkTo(State->GetInputPin());
	}
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(AnimBlueprint);
	return State->GetStateName();
}

FString UAgentToolkitGraphLibrary::AnimAddTransition(UAnimBlueprint* AnimBlueprint, const FString& MachineName, const FString& FromState,
	const FString& ToState, const FString& RuleType, FName VariableName, float CrossfadeDuration)
{
	UAnimGraphNode_StateMachineBase* Machine = AnimBlueprint ? FindStateMachine(AnimBlueprint, MachineName) : nullptr;
	if (!Machine)
	{
		return Fail(FString::Printf(TEXT("state machine '%s' not found"), *MachineName));
	}
	UAnimationStateMachineGraph* MachineGraph = Machine->EditorStateMachineGraph;
	UAnimStateNodeBase* From = FindState(MachineGraph, FromState);
	UAnimStateNodeBase* To = FindState(MachineGraph, ToState);
	if (!From || !To || From == To)
	{
		return Fail(FString::Printf(TEXT("states '%s' -> '%s' not found or identical"), *FromState, *ToState));
	}
	const FString Rule = RuleType.ToLower();
	if (Rule != TEXT("bool") && Rule != TEXT("not_bool") && Rule != TEXT("auto") && Rule != TEXT("always"))
	{
		return Fail(TEXT("RuleType must be bool, not_bool, auto or always"));
	}
	if ((Rule == TEXT("bool") || Rule == TEXT("not_bool")) && (VariableName.IsNone() || !HasVariable(AnimBlueprint, VariableName)))
	{
		return Fail(FString::Printf(TEXT("bool rule needs an existing variable, '%s' not found"), *VariableName.ToString()));
	}

	MachineGraph->Modify();
	FGraphNodeCreator<UAnimStateTransitionNode> Creator(*MachineGraph);
	UAnimStateTransitionNode* Transition = Creator.CreateNode();
	Creator.Finalize();
	Transition->CreateConnections(From, To);
	Transition->CrossfadeDuration = FMath::Max(0.f, CrossfadeDuration);

	UAnimationTransitionGraph* RuleGraph = Cast<UAnimationTransitionGraph>(Transition->BoundGraph);
	UAnimGraphNode_TransitionResult* Result = RuleGraph ? RuleGraph->GetResultNode() : nullptr;
	UEdGraphPin* CanEnter = Result ? Result->FindPin(TEXT("bCanEnterTransition")) : nullptr;
	if (Rule == TEXT("auto"))
	{
		Transition->bAutomaticRuleBasedOnSequencePlayerInState = true;
	}
	else if (!CanEnter)
	{
		return Fail(TEXT("transition rule graph has no result pin"));
	}
	else if (Rule == TEXT("always"))
	{
		RuleGraph->GetSchema()->TrySetDefaultValue(*CanEnter, TEXT("true"));
	}
	else
	{
		UK2Node_VariableGet* Get = AddVariableGet(RuleGraph, VariableName, Result->NodePosX - 400, Result->NodePosY);
		UEdGraphPin* Value = Get->FindPin(VariableName);
		if (Rule == TEXT("not_bool"))
		{
			FGraphNodeCreator<UK2Node_CallFunction> NotCreator(*RuleGraph);
			UK2Node_CallFunction* NotNode = NotCreator.CreateNode();
			NotNode->FunctionReference.SetExternalMember(GET_FUNCTION_NAME_CHECKED(UKismetMathLibrary, Not_PreBool), UKismetMathLibrary::StaticClass());
			NotNode->NodePosX = Result->NodePosX - 200;
			NotNode->NodePosY = Result->NodePosY;
			NotCreator.Finalize();
			Connect(RuleGraph, Value, FirstPin(NotNode, EGPD_Input));
			Value = NotNode->GetReturnValuePin();
		}
		if (!Connect(RuleGraph, Value, CanEnter))
		{
			return Fail(FString::Printf(TEXT("variable '%s' is not a bool"), *VariableName.ToString()));
		}
	}
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(AnimBlueprint);
	return Transition->GetName();
}

FString UAgentToolkitGraphLibrary::AnimDescribeStateMachines(UAnimBlueprint* AnimBlueprint)
{
	if (!AnimBlueprint)
	{
		return Fail(TEXT("AnimBlueprint is required"));
	}
	TArray<UAnimGraphNode_StateMachineBase*> Machines;
	FBlueprintEditorUtils::GetAllNodesOfClass<UAnimGraphNode_StateMachineBase>(AnimBlueprint, Machines);
	TArray<TSharedPtr<FJsonValue>> MachineArray;
	for (UAnimGraphNode_StateMachineBase* Machine : Machines)
	{
		UAnimationStateMachineGraph* Graph = Machine ? Machine->EditorStateMachineGraph.Get() : nullptr;
		if (!Graph)
		{
			continue;
		}
		TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
		M->SetStringField(TEXT("name"), Graph->GetName());
		M->SetBoolField(TEXT("connected_to_output"), FirstPin(Machine, EGPD_Output) && FirstPin(Machine, EGPD_Output)->LinkedTo.Num() > 0);
		if (UEdGraphPin* EntryOut = Graph->EntryNode ? Graph->EntryNode->GetOutputPin() : nullptr)
		{
			if (EntryOut->LinkedTo.Num())
			{
				if (UAnimStateNodeBase* Entry = Cast<UAnimStateNodeBase>(EntryOut->LinkedTo[0]->GetOwningNode()))
				{
					M->SetStringField(TEXT("entry_state"), Entry->GetStateName());
				}
			}
		}
		TArray<TSharedPtr<FJsonValue>> States, Transitions;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (UAnimStateTransitionNode* Transition = Cast<UAnimStateTransitionNode>(Node))
			{
				TSharedRef<FJsonObject> T = MakeShared<FJsonObject>();
				T->SetStringField(TEXT("id"), Transition->GetName());
				T->SetStringField(TEXT("from"), Transition->GetPreviousState() ? Transition->GetPreviousState()->GetStateName() : FString());
				T->SetStringField(TEXT("to"), Transition->GetNextState() ? Transition->GetNextState()->GetStateName() : FString());
				T->SetStringField(TEXT("rule"), RuleSummary(Transition));
				T->SetNumberField(TEXT("crossfade"), Transition->CrossfadeDuration);
				Transitions.Add(MakeShared<FJsonValueObject>(T));
			}
			else if (UAnimStateNodeBase* State = Cast<UAnimStateNodeBase>(Node))
			{
				TSharedRef<FJsonObject> S = MakeShared<FJsonObject>();
				S->SetStringField(TEXT("name"), State->GetStateName());
				S->SetStringField(TEXT("class"), State->GetClass()->GetName());
				if (UEdGraph* Bound = State->GetBoundGraph())
				{
					for (UEdGraphNode* Inner : Bound->Nodes)
					{
						if (UAnimGraphNode_Base* AnimNode = Cast<UAnimGraphNode_Base>(Inner))
						{
							if (UAnimationAsset* Asset = AnimNode->GetAnimationAsset())
							{
								S->SetStringField(TEXT("animation"), Asset->GetPathName());
								break;
							}
						}
					}
				}
				States.Add(MakeShared<FJsonValueObject>(S));
			}
		}
		M->SetArrayField(TEXT("states"), States);
		M->SetArrayField(TEXT("transitions"), Transitions);
		MachineArray.Add(MakeShared<FJsonValueObject>(M));
	}
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	Root->SetArrayField(TEXT("state_machines"), MachineArray);
	return ToJson(Root);
}

FString UAgentToolkitGraphLibrary::MontageAddSection(UAnimMontage* Montage, FName SectionName, float StartTime)
{
	if (!Montage || SectionName.IsNone())
	{
		return Fail(TEXT("Montage and SectionName are required"));
	}
	if (StartTime < 0.f || StartTime > Montage->GetPlayLength())
	{
		return Fail(FString::Printf(TEXT("StartTime %.3f is outside the montage length %.3f"), StartTime, Montage->GetPlayLength()));
	}
	Montage->Modify();
	if (Montage->AddAnimCompositeSection(SectionName, StartTime) == INDEX_NONE)
	{
		return Fail(FString::Printf(TEXT("section '%s' already exists"), *SectionName.ToString()));
	}
	Montage->MarkPackageDirty();
	return FString();
}

TArray<FString> UAgentToolkitGraphLibrary::MontageGetSections(UAnimMontage* Montage)
{
	TArray<FString> Out;
	if (Montage)
	{
		for (const FCompositeSection& Section : Montage->CompositeSections)
		{
			Out.Add(FString::Printf(TEXT("%s|%.4f|%s"), *Section.SectionName.ToString(), Section.GetTime(), *Section.NextSectionName.ToString()));
		}
	}
	return Out;
}

USoundCue* UAgentToolkitGraphLibrary::CreateSoundCue(const FString& FolderPath, const FString& AssetName, USoundWave* SoundWave, bool bLooping)
{
	if (!SoundWave || AssetName.IsEmpty())
	{
		return nullptr;
	}
	USoundCueFactoryNew* Factory = NewObject<USoundCueFactoryNew>();
	Factory->InitialSoundWaves.Add(SoundWave);
	IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools").Get();
	USoundCue* Cue = Cast<USoundCue>(AssetTools.CreateAsset(AssetName, FolderPath, USoundCue::StaticClass(), Factory));
	if (Cue && bLooping)
	{
		for (USoundNode* Node : Cue->AllNodes)
		{
			if (USoundNodeWavePlayer* Player = Cast<USoundNodeWavePlayer>(Node))
			{
				Player->bLooping = true;
			}
		}
		Cue->MarkPackageDirty();
	}
	return Cue;
}

FString UAgentToolkitGraphLibrary::BlendSpaceSetAxes(UBlendSpace* BlendSpace, const FString& XName, float XMin, float XMax,
	const FString& YName, float YMin, float YMax)
{
	if (!BlendSpace || XMax <= XMin || (!YName.IsEmpty() && YMax <= YMin))
	{
		return Fail(TEXT("valid BlendSpace and Min < Max ranges are required"));
	}
	FStructProperty* Prop = FindFProperty<FStructProperty>(UBlendSpace::StaticClass(), TEXT("BlendParameters"));
	if (!Prop)
	{
		return Fail(TEXT("BlendParameters property not found"));
	}
	BlendSpace->Modify();
	FBlendParameter* Params = Prop->ContainerPtrToValuePtr<FBlendParameter>(BlendSpace);
	Params[0].DisplayName = XName;
	Params[0].Min = XMin;
	Params[0].Max = XMax;
	if (!YName.IsEmpty())
	{
		Params[1].DisplayName = YName;
		Params[1].Min = YMin;
		Params[1].Max = YMax;
	}
	BlendSpace->PostEditChange();
	BlendSpace->MarkPackageDirty();
	return FString();
}

FString UAgentToolkitGraphLibrary::BlendSpaceAddSample(UBlendSpace* BlendSpace, UAnimSequence* Animation, float X, float Y)
{
	if (!BlendSpace || !Animation)
	{
		return Fail(TEXT("BlendSpace and Animation are required"));
	}
	if (BlendSpace->GetSkeleton() && Animation->GetSkeleton() && !BlendSpace->GetSkeleton()->IsCompatibleForEditor(Animation->GetSkeleton()))
	{
		return Fail(TEXT("animation skeleton is incompatible with the blend space"));
	}
	BlendSpace->Modify();
	if (BlendSpace->AddSample(Animation, FVector(X, Y, 0.f)) == INDEX_NONE)
	{
		return Fail(TEXT("sample rejected (outside the axis range or duplicate position)"));
	}
	BlendSpace->PostEditChange();
	BlendSpace->MarkPackageDirty();
	return FString();
}
