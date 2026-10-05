#pragma once

#include "CoreMinimal.h"
#include "EdGraph/EdGraphPin.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "AgentToolkitNativeLibrary.generated.h"

class UBlueprint;
class UK2Node_CustomEvent;

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
};
