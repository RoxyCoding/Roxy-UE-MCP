using UnrealBuildTool;

// Editor-only helpers exposed to Python for the few operations the Python API cannot reach.
public class UEAgentToolkitNative : ModuleRules
{
	public UEAgentToolkitNative(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"UnrealEd",
			"BlueprintGraph",
			"Kismet",
			"KismetCompiler",
			"MessageLog",
			"Json",
			"AIModule",
			"AIGraph",
			"BehaviorTreeEditor",
			"AnimGraph",
			"AnimGraphRuntime",
			"GraphEditor",
			"AssetTools",
			"AudioEditor",
			"UMG",
			"UMGEditor",
			"MovieScene",
			"MovieSceneTracks",
			"Landscape",
			"Foliage",
			"ImageWrapper",
			"RenderCore",
			"RHI",
			"MetasoundEngine",
			"MetasoundFrontend",
			"MetasoundGraphCore",
			"MetasoundEditor",
		});
	}
}
