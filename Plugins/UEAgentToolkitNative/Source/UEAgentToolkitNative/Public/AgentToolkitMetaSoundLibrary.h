#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "AgentToolkitMetaSoundLibrary.generated.h"

/**
 * Partial editing of existing MetaSound assets (Source or Patch) addressed by node GUIDs, which
 * the Python Builder API cannot expose. Exposed as unreal.AgentToolkitMetaSoundLibrary.
 * FString results are empty / a created id on success and "ERROR: <reason>" on failure.
 */
UCLASS()
class UEAGENTTOOLKITNATIVE_API UAgentToolkitMetaSoundLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * JSON of the default graph: nodes (id, name, class, kind, inputs with type/default/link, outputs),
	 * edges ("NodeId.Pin" -> "NodeId.Pin"), graph inputs and outputs.
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|MetaSound")
	static FString DescribeGraph(UObject* MetaSound);

	/** Adds a node by class name "Namespace.Name[.Variant]" (e.g. "UE.Sine.Audio"). Returns the node id. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|MetaSound")
	static FString AddNode(UObject* MetaSound, const FString& ClassName, int32 MajorVersion, FVector2D Location);

	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|MetaSound")
	static FString RemoveNode(UObject* MetaSound, const FString& NodeId);

	/** Connects FromNode.OutputName to ToNode.InputName (graph input/output nodes use their ids too). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|MetaSound")
	static FString Connect(UObject* MetaSound, const FString& FromNodeId, FName OutputName, const FString& ToNodeId, FName InputName);

	/** Removes the connection feeding ToNode.InputName. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|MetaSound")
	static FString Disconnect(UObject* MetaSound, const FString& ToNodeId, FName InputName);

	/**
	 * Sets a node input default. Value is parsed by the pin data type: Bool, Int32, Float/Time, String,
	 * or an asset path for object types (e.g. WaveAsset). An empty Value clears the default.
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|MetaSound")
	static FString SetInputDefault(UObject* MetaSound, const FString& NodeId, FName InputName, const FString& Value);

	/** Adds a graph input (exposed parameter). Returns the id of the created input node. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|MetaSound")
	static FString AddGraphInput(UObject* MetaSound, FName Name, FName DataType, const FString& DefaultValue);

	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|MetaSound")
	static FString RemoveGraphInput(UObject* MetaSound, FName Name);
};
