#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "AgentToolkitGraphLibrary.generated.h"

class UAnimBlueprint;
class UAnimMontage;
class UAnimSequence;
class UAnimationAsset;
class UBehaviorTree;
class UBlendSpace;
class UBlackboardData;
class USkeleton;
class USoundCue;
class USoundWave;

/**
 * Graph authoring helpers for asset types without a Python editing API
 * (Behavior Tree graphs, Animation Blueprint state machines, montage sections, blend spaces).
 * Exposed to Python as unreal.AgentToolkitGraphLibrary. Functions returning FString return an
 * empty string (or the created node name) on success and "ERROR: <reason>" on failure.
 */
UCLASS()
class UEAGENTTOOLKITNATIVE_API UAgentToolkitGraphLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// ---------------------------------------------------------------- Behavior Tree
	/**
	 * Adds a composite or task node under ParentNodeName ("" or "Root" = root). Returns the new graph node name.
	 * ParentOutputIndex selects the parent's output: for Simple Parallel 0 = main task (single task), 1 = background.
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|BehaviorTree")
	static FString BTAddNode(UBehaviorTree* BehaviorTree, const FString& ParentNodeName, UClass* NodeClass, int32 ParentOutputIndex = 0);

	/** Adds a decorator or service to a composite/task graph node. Returns the new sub-node name. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|BehaviorTree")
	static FString BTAddSubNode(UBehaviorTree* BehaviorTree, const FString& OwnerNodeName, UClass* SubNodeClass);

	/** Removes a node (with its sub-nodes) from the graph. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|BehaviorTree")
	static FString BTRemoveNode(UBehaviorTree* BehaviorTree, const FString& NodeName);

	/** Returns the runtime node instance (BTTask/BTComposite/BTDecorator/BTService object) of a graph node. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|BehaviorTree")
	static UObject* BTGetNodeInstance(UBehaviorTree* BehaviorTree, const FString& NodeName);

	/** Sets a FBlackboardKeySelector property of a node instance to a key of the tree's blackboard. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|BehaviorTree")
	static FString BTSetBlackboardKey(UBehaviorTree* BehaviorTree, const FString& NodeName, FName PropertyName, FName KeyName);

	/** Rebuilds the runtime tree from the graph (call after edits). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|BehaviorTree")
	static FString BTUpdateAsset(UBehaviorTree* BehaviorTree);

	/** JSON description of the editor graph: nodes, classes, children (execution order), decorators, services. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|BehaviorTree")
	static FString BTDescribeGraph(UBehaviorTree* BehaviorTree);

	/** Adds a blackboard key. KeyType: bool, int, float, string, name, vector, rotator, object, class, enum. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|BehaviorTree")
	static FString BBAddKey(UBlackboardData* Blackboard, FName KeyName, const FString& KeyType, UObject* BaseClassOrEnum, bool bInstanceSynced);

	/** Removes a blackboard key declared in this blackboard (not inherited). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|BehaviorTree")
	static FString BBRemoveKey(UBlackboardData* Blackboard, FName KeyName);

	/** JSON list of keys (name, type, base class/enum, instance synced, inherited). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|BehaviorTree")
	static FString BBDescribeKeys(UBlackboardData* Blackboard);

	// ------------------------------------------------------------- Anim Blueprint
	/** Creates an Animation Blueprint for a skeleton (ParentClass defaults to AnimInstance). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Animation")
	static UAnimBlueprint* CreateAnimBlueprint(const FString& FolderPath, const FString& AssetName, USkeleton* Skeleton, UClass* ParentClass);
	/** Adds a state machine to the AnimGraph; optionally connects it to the Output Pose. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Animation")
	static FString AnimAddStateMachine(UAnimBlueprint* AnimBlueprint, const FString& MachineName, bool bConnectToOutputPose);

	/**
	 * Adds a state. AnimationAsset (sequence or blend space) is placed in the state graph and wired to
	 * the state result. BlendSpaceXVariable/YVariable name float member variables driving blend space axes.
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Animation")
	static FString AnimAddState(UAnimBlueprint* AnimBlueprint, const FString& MachineName, const FString& StateName,
		UAnimationAsset* AnimationAsset, FName BlendSpaceXVariable, FName BlendSpaceYVariable, bool bSetAsEntryState);

	/**
	 * Adds a transition. RuleType: "bool" (VariableName true), "not_bool" (VariableName false),
	 * "auto" (when the source state's animation is about to finish), "always".
	 */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Animation")
	static FString AnimAddTransition(UAnimBlueprint* AnimBlueprint, const FString& MachineName, const FString& FromState,
		const FString& ToState, const FString& RuleType, FName VariableName, float CrossfadeDuration);

	/** JSON description of all state machines: states (+animation asset), entry state, transitions (+rule). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Animation")
	static FString AnimDescribeStateMachines(UAnimBlueprint* AnimBlueprint);

	/** Adds a montage section at StartTime (seconds). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Animation")
	static FString MontageAddSection(UAnimMontage* Montage, FName SectionName, float StartTime);

	/** Montage sections as "Name|StartTime|NextSection". */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Animation")
	static TArray<FString> MontageGetSections(UAnimMontage* Montage);

	/** Creates a Sound Cue playing SoundWave (Wave Player node, optionally looping). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Audio")
	static USoundCue* CreateSoundCue(const FString& FolderPath, const FString& AssetName, USoundWave* SoundWave, bool bLooping);

	/** Sets blend space axis names/ranges (Y ignored for 1D blend spaces). */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Animation")
	static FString BlendSpaceSetAxes(UBlendSpace* BlendSpace, const FString& XName, float XMin, float XMax,
		const FString& YName, float YMin, float YMax);

	/** Adds a sample (animation at X,Y) to a blend space. */
	UFUNCTION(BlueprintCallable, Category = "AgentToolkit|Animation")
	static FString BlendSpaceAddSample(UBlendSpace* BlendSpace, UAnimSequence* Animation, float X, float Y);
};
