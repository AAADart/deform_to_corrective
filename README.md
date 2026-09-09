# Deform to Corrective

A Blender 5.x extension for baking evaluated deformation modifiers into an editable corrective Shape Key while keeping the rig and preceding deformation stack live.

## What it does

**Deform to Corrective** turns the visible result of Cloth, Lattice, Wave, Simple Deform, Surface Deform, Shrinkwrap and other deformation modifiers into a regular editable Shape Key that lives before the rig.

The main workflow is intentionally simple:

1. Pose the character and set deformation modifiers to the result you want.
2. Press **Save as Corrective** in **Properties > Modifiers** or in **3D View > N-panel > Deform**.
3. The extension detects the rig, captures the selected deformation modifiers, numerically inverts the live pre-deformation stack, and creates one corrective Shape Key.
4. After a successful bake, the baked deformation modifiers are disabled in Viewport and Render and the new Shape Key is set to `1.0` for immediate comparison.

No duplicate mesh, applied Armature, or temporary RAW Shape Key is required.

## Features

- One-click **Save as Corrective** workflow.
- Automatic rig detection with manual override.
- Multi-selection of deformation modifiers.
- All enabled supported deformers are selected by default.
- Multiple deformers are baked as one combined result, not one by one.
- Keeps Armature, Hook and other preceding deformation modifiers live.
- Numerical per-vertex `3x3` Jacobian inverse deformation solver.
- Refinement iterations for improved accuracy.
- Automatic or manual epsilon control.
- Max and mean bake error reporting.
- Preserves the original modifier and Shape Key state if the operation fails.
- Supports relative Shape Keys.
- Works with non-unit object scale.

## Multi-deformer workflow

For a stack such as:

```text
Armature
Hook
Lattice
Wave
Simple Deform
Subdivision
```

selecting `Lattice`, `Wave`, and `Simple Deform` captures their combined evaluated result once and solves it into a single corrective Shape Key. The selected deformation modifiers are then disabled together while the Armature and preceding live stack remain editable.

Unselected modifiers inside the solve range stay live. Modifiers below the last selected deformation are excluded from the target capture and restored afterward.

## Controls

### Quick action

**Properties > Modifiers > Save as Corrective**

### Detailed controls

**3D View > N-panel > Deform > Deform to Corrective**

- **Rig Modifier** — Auto or explicit rig/pre-deformation anchor.
- **Deformation Modifiers** — choose which enabled deformation modifiers to bake.
- **All Enabled** — restore automatic selection of all supported enabled deformers.
- **Output Shape Key** — name of the generated corrective.
- **Iterations** — numerical refinement iterations.
- **Advanced > Epsilon** — Jacobian probe size.
- **Last Bake** — max and mean error statistics.

## Installation

Download the extension ZIP and install it in Blender using:

**Edit > Preferences > Get Extensions > Install from Disk**

The repository currently targets **Blender 5.0+**.

## Notes and limitations

- The captured deformation and inverse stack must preserve vertex count/topology.
- Known topology-changing modifiers are excluded from automatic deformation selection.
- Negative object scale is not currently a supported/tested workflow.
- Complex stacks may require more refinement iterations or manual epsilon adjustment.

## Version

Current development version: **0.3.0**

## License

GPL-3.0-or-later.
