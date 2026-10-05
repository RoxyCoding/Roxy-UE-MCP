#pragma once

#include "CoreMinimal.h"
#include "EdGraph/EdGraphPin.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "AgentToolkitNativeLibrary.generated.h"

class UBlueprint;
class UK2Node_CustomEvent;
class UK2Node_CallFunction;
class UK2Node_CreateDelegate;
class UEdGraphNode;

/**
 * Editor helpers used by the UE Agent Toolkit Python toolsets (unreal.AgentToolkitNativeLibrary).
 * Only operations without a Python API live here; everything else is implemented in Python.
 */
UCLASS()
class UEAGENTTOOLKITNATIVE_API UAgentToolkitNativeLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Adds an interface (native UInterface or Blueprint Interface generated class) to a Blueprint. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static bool AddInterfaceToBlueprint(UBlueprint* Blueprint, UClass* InterfaceClass);

	/** Removes an implemented interface. bPreserveFunctions keeps the implemented graphs as normal functions. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static bool RemoveInterfaceFromBlueprint(UBlueprint* Blueprint, UClass* InterfaceClass, bool bPreserveFunctions);

	/** Interfaces implemented directly by the Blueprint (not inherited). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static TArray<UClass*> GetImplementedInterfaces(UBlueprint* Blueprint);

	/** Creates a new macro graph in the Blueprint. Returns the graph or null if the name is taken. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static UEdGraph* AddMacroGraph(UBlueprint* Blueprint, const FString& MacroName);

	/**
	 * Sets RPC replication of a custom event.
	 * @param NetMode 0 = not replicated, 1 = Run on Server, 2 = Run on owning Client, 3 = Multicast.
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static bool SetCustomEventReplication(UK2Node_CustomEvent* CustomEvent, int32 NetMode, bool bReliable);

	/** Adds a user-defined input (output pin on the node) to a custom event. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static bool AddCustomEventInput(UK2Node_CustomEvent* CustomEvent, FName InputName, const FEdGraphPinType& PinType);

	/** Returns "None", "Server", "Client" or "Multicast" plus ",Reliable" when reliable. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FString GetCustomEventReplication(UK2Node_CustomEvent* CustomEvent);

	/** Sets the lifetime replication condition (ELifetimeCondition value) of a Blueprint member variable. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static bool SetVariableReplicationCondition(UBlueprint* Blueprint, FName VariableName, int32 Condition);

	/** Returns the ELifetimeCondition value of a Blueprint member variable, or -1 if not found. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static int32 GetVariableReplicationCondition(UBlueprint* Blueprint, FName VariableName);

	/** Compiles the Blueprint and returns every compiler message as "Severity: text". */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Build")
	static TArray<FString> CompileBlueprintWithLog(UBlueprint* Blueprint);

	/** Returns messages of a Message Log listing (e.g. BlueprintLog, MapCheck, PIE) as "Severity: text". */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Build")
	static TArray<FString> GetMessageLogMessages(FName LogName);

	/** Names of all registered Message Log listings that currently have messages or are known. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Build")
	static TArray<FString> GetKnownMessageLogNames();

	// ------------------------------------------------------------ node pins / signatures
	// Functions returning FString return an empty string on success and "ERROR: <reason>" on failure.

	/** Adds an input pin to nodes with a variable pin count (Sequence, Make Array, Select, Switch, ...). Returns the new pin name. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FString AddNodePin(UEdGraphNode* Node);

	/** Removes a removable pin (by name) from nodes with a variable pin count. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FString RemoveNodePin(UEdGraphNode* Node, FName PinName);

	/** Points a Call Function node at the same function on another class (keeps links where pins still match). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FString RetargetCallFunctionClass(UK2Node_CallFunction* Node, UClass* NewClass);

	/** Adds an event dispatcher (multicast delegate variable with its signature graph). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FString AddEventDispatcher(UBlueprint* Blueprint, FName DispatcherName);

	/** Adds a parameter to a function graph or event dispatcher signature (input = function input / dispatcher parameter, else function output). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FString AddGraphParam(UBlueprint* Blueprint, FName GraphName, FName ParamName, const FEdGraphPinType& PinType, bool bOutput);

	/** Removes a parameter from a function graph or event dispatcher signature. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FString RemoveGraphParam(UBlueprint* Blueprint, FName GraphName, FName ParamName, bool bOutput);

	/** Function name currently assigned to a Create Event node. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FName GetCreateEventFunction(UK2Node_CreateDelegate* Node);

	/**
	 * Functions / custom events on the node's scope class whose parameters match the delegate signature
	 * (same parameter count and types, return values ignored). A candidate list; SetCreateEventFunction does the final check.
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static TArray<FString> ListCompatibleEventFunctions(UK2Node_CreateDelegate* Node);

	/** Assigns a function (on the node's target object class) to a Create Event node. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FString SetCreateEventFunction(UK2Node_CreateDelegate* Node, FName FunctionName);

	/**
	 * Member a node refers to, language independent: event / custom event function name,
	 * called function ("Class:Function"), or variable name. Empty for other nodes.
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FString GetNodeMemberName(UEdGraphNode* Node);

	/**
	 * Finds Blueprint-callable functions by English name/display name/keywords (case, spaces and
	 * underscores ignored) on ContextClass (with super classes) and every Blueprint Function Library.
	 * Returns JSON [{"id": "Class:Function", "display_name", "category", "pure", "static", "params", "return"}].
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Blueprint")
	static FString FindCallableFunctions(UClass* ContextClass, const FString& Query, int32 MaxResults);
};
