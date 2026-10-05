#include "AgentToolkitMetaSoundLibrary.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "MetasoundBuilderBase.h"
#include "MetasoundDocumentInterface.h"
#include "MetasoundEditorSubsystem.h"
#include "MetasoundFrontendDocument.h"
#include "MetasoundFrontendLiteral.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/StrongObjectPtr.h"

namespace AgentToolkitMetaSound
{
	static FString Fail(const FString& Message) { return TEXT("ERROR: ") + Message; }

	static const FMetasoundFrontendDocument* GetDocument(UObject* MetaSound)
	{
		if (MetaSound && MetaSound->GetClass()->ImplementsInterface(UMetaSoundDocumentInterface::StaticClass()))
		{
			const IMetaSoundDocumentInterface* Interface = Cast<IMetaSoundDocumentInterface>(MetaSound);
			return Interface ? &Interface->GetConstDocument() : nullptr;
		}
		return nullptr;
	}

	static UMetaSoundBuilderBase* GetBuilder(UObject* MetaSound, FString& Error)
	{
		if (!GetDocument(MetaSound))
		{
			Error = TEXT("object is not a MetaSound asset");
			return nullptr;
		}
		UMetaSoundEditorSubsystem* Subsystem = GEditor ? GEditor->GetEditorSubsystem<UMetaSoundEditorSubsystem>() : nullptr;
		if (!Subsystem)
		{
			Error = TEXT("MetaSound editor subsystem unavailable");
			return nullptr;
		}
		EMetaSoundBuilderResult Result = EMetaSoundBuilderResult::Failed;
		UMetaSoundBuilderBase* Builder = Subsystem->FindOrBeginBuilding(TScriptInterface<IMetaSoundDocumentInterface>(MetaSound), Result);
		if (!Builder || Result != EMetaSoundBuilderResult::Succeeded)
		{
			Error = TEXT("could not open a builder for this MetaSound (transient asset?)");
			return nullptr;
		}
		return Builder;
	}

	static bool ParseId(const FString& Text, FGuid& Out)
	{
		return FGuid::Parse(Text, Out);
	}

	static const FMetasoundFrontendNode* FindNode(const FMetasoundFrontendDocument& Doc, const FGuid& Id)
	{
		for (const FMetasoundFrontendNode& Node : Doc.RootGraph.GetConstDefaultGraph().Nodes)
		{
			if (Node.GetID() == Id)
			{
				return &Node;
			}
		}
		return nullptr;
	}

	static const FMetasoundFrontendClass* FindClass(const FMetasoundFrontendDocument& Doc, const FGuid& ClassId)
	{
		for (const FMetasoundFrontendClass& Class : Doc.Dependencies)
		{
			if (Class.ID == ClassId)
			{
				return &Class;
			}
		}
		return nullptr;
	}

	static const FMetasoundFrontendVertex* FindVertex(const TArray<FMetasoundFrontendVertex>& Vertices, FName Name)
	{
		for (const FMetasoundFrontendVertex& V : Vertices)
		{
			if (V.Name == Name)
			{
				return &V;
			}
		}
		return nullptr;
	}

	static FString KindName(EMetasoundFrontendClassType Type)
	{
		switch (Type)
		{
		case EMetasoundFrontendClassType::Input: return TEXT("graph_input");
		case EMetasoundFrontendClassType::Output: return TEXT("graph_output");
		case EMetasoundFrontendClassType::Variable: return TEXT("variable");
		case EMetasoundFrontendClassType::Literal: return TEXT("literal");
		default: return TEXT("node");
		}
	}

	/** Builds a literal for a pin data type from text. */
	static bool MakeLiteral(FName DataType, const FString& Value, FMetasoundFrontendLiteral& Out, FString& Error)
	{
		const FString Type = DataType.ToString();
		if (Type == TEXT("Bool") || Type == TEXT("Trigger"))
		{
			Out.Set(Value.ToBool() || Value.Equals(TEXT("1")));
		}
		else if (Type == TEXT("Int32"))
		{
			Out.Set(FCString::Atoi(*Value));
		}
		else if (Type == TEXT("Float") || Type == TEXT("Time") || Type == TEXT("Frequency"))
		{
			Out.Set(FCString::Atof(*Value));
		}
		else if (Type == TEXT("String"))
		{
			Out.Set(Value);
		}
		else if (Value.StartsWith(TEXT("/")))
		{
			UObject* Asset = LoadObject<UObject>(nullptr, *Value);
			if (!Asset)
			{
				Error = FString::Printf(TEXT("asset '%s' could not be loaded"), *Value);
				return false;
			}
			Out.Set(Asset);
		}
		else
		{
			Error = FString::Printf(TEXT("unsupported data type '%s' for a text default"), *Type);
			return false;
		}
		return true;
	}

	static FString ToJson(const TSharedRef<FJsonObject>& Object)
	{
		FString Out;
		const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Out);
		FJsonSerializer::Serialize(Object, Writer);
		return Out;
	}

	static FString Finish(UObject* MetaSound, EMetaSoundBuilderResult Result, const FString& What, const FString& Success = FString())
	{
		if (Result != EMetaSoundBuilderResult::Succeeded)
		{
			return Fail(What + TEXT(" failed (wrong class/pin name, data type mismatch, or existing connection)"));
		}
		MetaSound->Modify();
		MetaSound->MarkPackageDirty();
		return Success;
	}
}

using namespace AgentToolkitMetaSound;

FString UAgentToolkitMetaSoundLibrary::DescribeGraph(UObject* MetaSound)
{
	const FMetasoundFrontendDocument* Doc = GetDocument(MetaSound);
	if (!Doc)
	{
		return Fail(TEXT("object is not a MetaSound asset"));
	}
	const FMetasoundFrontendGraph& Graph = Doc->RootGraph.GetConstDefaultGraph();
	TMap<FGuid, FString> NodeNames;
	TMap<FString, FString> IncomingByInput;  // "ToNode.VertexId" -> "FromNode.Pin"
	auto VertexName = [&Graph](const FGuid& NodeId, const FGuid& VertexId, bool bOutput) -> FString
	{
		for (const FMetasoundFrontendNode& Node : Graph.Nodes)
		{
			if (Node.GetID() == NodeId)
			{
				for (const FMetasoundFrontendVertex& V : bOutput ? Node.Interface.Outputs : Node.Interface.Inputs)
				{
					if (V.VertexID == VertexId) { return V.Name.ToString(); }
				}
			}
		}
		return VertexId.ToString();
	};

	TArray<TSharedPtr<FJsonValue>> Edges;
	for (const FMetasoundFrontendEdge& Edge : Graph.Edges)
	{
		const FString From = Edge.FromNodeID.ToString(EGuidFormats::Digits) + TEXT(".") + VertexName(Edge.FromNodeID, Edge.FromVertexID, true);
		const FString To = Edge.ToNodeID.ToString(EGuidFormats::Digits) + TEXT(".") + VertexName(Edge.ToNodeID, Edge.ToVertexID, false);
		IncomingByInput.Add(Edge.ToNodeID.ToString(EGuidFormats::Digits) + TEXT(".") + Edge.ToVertexID.ToString(), From);
		Edges.Add(MakeShared<FJsonValueString>(From + TEXT("->") + To));
	}

	TArray<TSharedPtr<FJsonValue>> Nodes, GraphInputs, GraphOutputs;
	for (const FMetasoundFrontendNode& Node : Graph.Nodes)
	{
		const FString Id = Node.GetID().ToString(EGuidFormats::Digits);
		const FMetasoundFrontendClass* Class = FindClass(*Doc, Node.ClassID);
		TSharedRef<FJsonObject> N = MakeShared<FJsonObject>();
		N->SetStringField(TEXT("id"), Id);
		N->SetStringField(TEXT("name"), Node.Name.ToString());
		N->SetStringField(TEXT("class"), Class ? Class->Metadata.GetClassName().ToString() : TEXT("?"));
		const FString Kind = Class ? KindName(Class->Metadata.GetType()) : TEXT("node");
		N->SetStringField(TEXT("kind"), Kind);
		TArray<TSharedPtr<FJsonValue>> Inputs, Outputs;
		for (const FMetasoundFrontendVertex& V : Node.Interface.Inputs)
		{
			TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
			P->SetStringField(TEXT("name"), V.Name.ToString());
			P->SetStringField(TEXT("type"), V.TypeName.ToString());
			for (const FMetasoundFrontendVertexLiteral& Literal : Node.InputLiterals)
			{
				if (Literal.VertexID == V.VertexID)
				{
					P->SetStringField(TEXT("default"), Literal.Value.ToString());
				}
			}
			if (const FString* Link = IncomingByInput.Find(Id + TEXT(".") + V.VertexID.ToString()))
			{
				P->SetStringField(TEXT("link"), *Link);
			}
			Inputs.Add(MakeShared<FJsonValueObject>(P));
		}
		for (const FMetasoundFrontendVertex& V : Node.Interface.Outputs)
		{
			TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
			P->SetStringField(TEXT("name"), V.Name.ToString());
			P->SetStringField(TEXT("type"), V.TypeName.ToString());
			Outputs.Add(MakeShared<FJsonValueObject>(P));
		}
		N->SetArrayField(TEXT("inputs"), Inputs);
		N->SetArrayField(TEXT("outputs"), Outputs);
		Nodes.Add(MakeShared<FJsonValueObject>(N));
		if (Kind == TEXT("graph_input")) { GraphInputs.Add(MakeShared<FJsonValueString>(Node.Name.ToString())); }
		if (Kind == TEXT("graph_output")) { GraphOutputs.Add(MakeShared<FJsonValueString>(Node.Name.ToString())); }
	}
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	Root->SetArrayField(TEXT("nodes"), Nodes);
	Root->SetArrayField(TEXT("edges"), Edges);
	Root->SetArrayField(TEXT("graph_inputs"), GraphInputs);
	Root->SetArrayField(TEXT("graph_outputs"), GraphOutputs);
	return ToJson(Root);
}

FString UAgentToolkitMetaSoundLibrary::AddNode(UObject* MetaSound, const FString& ClassName, int32 MajorVersion, FVector2D Location)
{
	FString Error;
	UMetaSoundBuilderBase* Builder = GetBuilder(MetaSound, Error);
	if (!Builder)
	{
		return Fail(Error);
	}
	TArray<FString> Parts;
	ClassName.ParseIntoArray(Parts, TEXT("."));
	if (Parts.Num() < 2)
	{
		return Fail(TEXT("ClassName must be Namespace.Name[.Variant], e.g. UE.Sine.Audio"));
	}
	const FString Variant = Parts.Num() > 2 ? FString::Join(TArrayView<const FString>(Parts).RightChop(2), TEXT(".")) : FString();
	const FMetasoundFrontendClassName Name{ FName(*Parts[0]), FName(*Parts[1]), FName(*Variant) };
	EMetaSoundBuilderResult Result = EMetaSoundBuilderResult::Failed;
	const FMetaSoundNodeHandle Handle = Builder->AddNodeByClassName(Name, Result, FMath::Max(1, MajorVersion));
	if (Result != EMetaSoundBuilderResult::Succeeded)
	{
		return Fail(FString::Printf(TEXT("node class '%s' (v%d) not found"), *ClassName, MajorVersion));
	}
	if (UMetaSoundEditorSubsystem* Subsystem = GEditor->GetEditorSubsystem<UMetaSoundEditorSubsystem>())
	{
		EMetaSoundBuilderResult LocationResult;
		Subsystem->SetNodeLocation(Builder, Handle, Location, LocationResult);
	}
	return Finish(MetaSound, Result, TEXT("AddNode"), Handle.NodeID.ToString(EGuidFormats::Digits));
}

FString UAgentToolkitMetaSoundLibrary::RemoveNode(UObject* MetaSound, const FString& NodeId)
{
	FString Error;
	UMetaSoundBuilderBase* Builder = GetBuilder(MetaSound, Error);
	FGuid Id;
	if (!Builder)
	{
		return Fail(Error);
	}
	if (!ParseId(NodeId, Id) || !FindNode(*GetDocument(MetaSound), Id))
	{
		return Fail(FString::Printf(TEXT("node '%s' not found"), *NodeId));
	}
	EMetaSoundBuilderResult Result = EMetaSoundBuilderResult::Failed;
	Builder->RemoveNode(FMetaSoundNodeHandle(Id), Result);
	return Finish(MetaSound, Result, TEXT("RemoveNode"));
}

FString UAgentToolkitMetaSoundLibrary::Connect(UObject* MetaSound, const FString& FromNodeId, FName OutputName, const FString& ToNodeId, FName InputName)
{
	FString Error;
	UMetaSoundBuilderBase* Builder = GetBuilder(MetaSound, Error);
	if (!Builder)
	{
		return Fail(Error);
	}
	const FMetasoundFrontendDocument& Doc = *GetDocument(MetaSound);
	FGuid From, To;
	const FMetasoundFrontendNode* FromNode = ParseId(FromNodeId, From) ? FindNode(Doc, From) : nullptr;
	const FMetasoundFrontendNode* ToNode = ParseId(ToNodeId, To) ? FindNode(Doc, To) : nullptr;
	if (!FromNode || !ToNode)
	{
		return Fail(TEXT("source or destination node not found"));
	}
	const FMetasoundFrontendVertex* Out = FindVertex(FromNode->Interface.Outputs, OutputName);
	const FMetasoundFrontendVertex* In = FindVertex(ToNode->Interface.Inputs, InputName);
	if (!Out || !In)
	{
		return Fail(FString::Printf(TEXT("pin not found: output '%s' or input '%s'"), *OutputName.ToString(), *InputName.ToString()));
	}
	if (Out->TypeName != In->TypeName)
	{
		return Fail(FString::Printf(TEXT("data type mismatch: %s -> %s"), *Out->TypeName.ToString(), *In->TypeName.ToString()));
	}
	EMetaSoundBuilderResult Result = EMetaSoundBuilderResult::Failed;
	Builder->ConnectNodes(FMetaSoundBuilderNodeOutputHandle(From, Out->VertexID), FMetaSoundBuilderNodeInputHandle(To, In->VertexID), Result);
	return Finish(MetaSound, Result, TEXT("Connect"));
}

FString UAgentToolkitMetaSoundLibrary::Disconnect(UObject* MetaSound, const FString& ToNodeId, FName InputName)
{
	FString Error;
	UMetaSoundBuilderBase* Builder = GetBuilder(MetaSound, Error);
	if (!Builder)
	{
		return Fail(Error);
	}
	FGuid To;
	const FMetasoundFrontendNode* ToNode = ParseId(ToNodeId, To) ? FindNode(*GetDocument(MetaSound), To) : nullptr;
	const FMetasoundFrontendVertex* In = ToNode ? FindVertex(ToNode->Interface.Inputs, InputName) : nullptr;
	if (!In)
	{
		return Fail(FString::Printf(TEXT("input '%s' not found on node %s"), *InputName.ToString(), *ToNodeId));
	}
	const FMetaSoundBuilderNodeInputHandle Handle(To, In->VertexID);
	if (!Builder->NodeInputIsConnected(Handle))
	{
		return Fail(TEXT("input is not connected"));
	}
	EMetaSoundBuilderResult Result = EMetaSoundBuilderResult::Failed;
	Builder->DisconnectNodeInput(Handle, Result);
	return Finish(MetaSound, Result, TEXT("Disconnect"));
}

FString UAgentToolkitMetaSoundLibrary::SetInputDefault(UObject* MetaSound, const FString& NodeId, FName InputName, const FString& Value)
{
	FString Error;
	UMetaSoundBuilderBase* Builder = GetBuilder(MetaSound, Error);
	if (!Builder)
	{
		return Fail(Error);
	}
	FGuid Id;
	const FMetasoundFrontendNode* Node = ParseId(NodeId, Id) ? FindNode(*GetDocument(MetaSound), Id) : nullptr;
	const FMetasoundFrontendVertex* In = Node ? FindVertex(Node->Interface.Inputs, InputName) : nullptr;
	if (!In)
	{
		return Fail(FString::Printf(TEXT("input '%s' not found on node %s"), *InputName.ToString(), *NodeId));
	}
	const FMetaSoundBuilderNodeInputHandle Handle(Id, In->VertexID);
	EMetaSoundBuilderResult Result = EMetaSoundBuilderResult::Failed;
	if (Value.IsEmpty())
	{
		Builder->RemoveNodeInputDefault(Handle, Result);
		return Finish(MetaSound, Result, TEXT("RemoveNodeInputDefault"));
	}
	FMetasoundFrontendLiteral Literal;
	if (!MakeLiteral(In->TypeName, Value, Literal, Error))
	{
		return Fail(Error);
	}
	Builder->SetNodeInputDefault(Handle, Literal, Result);
	return Finish(MetaSound, Result, TEXT("SetNodeInputDefault"));
}

FString UAgentToolkitMetaSoundLibrary::AddGraphInput(UObject* MetaSound, FName Name, FName DataType, const FString& DefaultValue)
{
	FString Error;
	UMetaSoundBuilderBase* Builder = GetBuilder(MetaSound, Error);
	if (!Builder)
	{
		return Fail(Error);
	}
	FMetasoundFrontendLiteral Literal;
	if (!DefaultValue.IsEmpty() && !MakeLiteral(DataType, DefaultValue, Literal, Error))
	{
		return Fail(Error);
	}
	EMetaSoundBuilderResult Result = EMetaSoundBuilderResult::Failed;
	const FMetaSoundBuilderNodeOutputHandle Out = Builder->AddGraphInputNode(Name, DataType, Literal, Result);
	return Finish(MetaSound, Result, TEXT("AddGraphInput (name taken or unknown data type?)"), Out.NodeID.ToString(EGuidFormats::Digits));
}

FString UAgentToolkitMetaSoundLibrary::RemoveGraphInput(UObject* MetaSound, FName Name)
{
	FString Error;
	UMetaSoundBuilderBase* Builder = GetBuilder(MetaSound, Error);
	if (!Builder)
	{
		return Fail(Error);
	}
	EMetaSoundBuilderResult Result = EMetaSoundBuilderResult::Failed;
	Builder->RemoveGraphInput(Name, Result);
	return Finish(MetaSound, Result, FString::Printf(TEXT("RemoveGraphInput '%s'"), *Name.ToString()));
}
