# p-11B Reactor Core Simulator -- Tutorial & Narrative Guide

This guide walks through the dashboard: the universal colored-particle legend,
then a narrative for each of the three reactor concepts (physical architecture,
control inputs, what the particles are doing, and the output measurements).

> Launch with `./pb11_reactor_sim/run.sh`, pick a reactor, set sliders (or
> **Optimize**), then **Compile** to pre-render the full shot (video + voice in
> sync). **Rec Start** → **Play** or **Step** (numbered callouts `1/N` … `N/N`,
> one segment at a time; **Back** revisits the previous step) → **Rec Save** for
> MP4. Re-compile after you change sliders or reactor. **Skip to flat-top**
> (or pulse / pinch) applies only to a live sim countdown, not compiled review.

---

## Simulation buttons (all reactors)

The chamber starts **unarmed** at launch. Use the **Simulation** column on the
left in this order (top to bottom):

| Button | What it does |
|--------|----------------|
| **Optimize** | Search that reactor's sliders for best steady-state `Q_net` (background thread). Sliders move to the optimum; status bar reports the result. |
| **Compile** | Run the full shot once in the background: one snapshot per phase, narration-first stretch, facility bed + cached ChatTTS — same recipe as MP4 export. Required before **Play** / **Step** / **Back**. Invalidated if you change reactor or sliders. |
| **Play** | Play the full compiled shot from step **1/N** (Arm) through **N/N** (quiescent), segment after segment. |
| **Step** | Play one numbered segment per press (Arm first, then each fire phase, then quiescent). |
| **Back** | Jump to the previous segment and pause on its first frame. |
| **Rec Start** | Begin capturing frames (canvas + three diagnostic plots). Disabled while a capture is active. |
| **Rec Save** | Stop capture and save MP4 (numbered subtitles + synced audio on export). Enabled after **Rec Start**. |

**Skip to …** (when visible): jumps a *live* sim countdown to flat-top (TAE), laser pulse (HB11), or pinch (LPP). Not used during compiled playback.

### Repeat shots (TAE vs HB11 / LPP)

| Reactor | Full re-arm for next shot? | Practice |
|---------|---------------------------|----------|
| **TAE FRC** | **No** (live model) | After quiescence, re-**Compile** from quiescent for a shortened countdown; mimics repeated experimental shots in one vacuum day. |
| **HB11 Laser** | **Yes** | Each shot consumes the target; re-condition chamber and re-**Compile**. |
| **LPP DPF** | **Yes** | Bank depleted; recharge/refill and re-**Compile**. |

Re-**Compile** whenever sliders change so cached playback matches your controls.

The **Status** line in Live Readout is the operator callout (e.g. `T−1: NBI on`, `PINCH — focus on axis`). Countdown labels like **T−5 s** are control-room shorthand, not wall-clock seconds — pre-discharge sim time is compressed so you reach the discharge in a few seconds of real time, not a minute.

---

## The p-¹¹B reaction is a 4-stage chain (and why that matters here)

It is tempting to write the reaction as a single step, `p + ¹¹B → 3α + 8.7 MeV`.
In reality it is a **sequential decay through short-lived intermediate nuclei**,
and that internal structure is what gives the fusion products their
characteristic energy distribution.

### The four stages

```
  Stage 1: ¹H + ¹¹B  →  ¹²C*                 (fusion forms an excited compound nucleus)
  Stage 2: ¹²C*      →  α  +  ⁸Be(*)         (emits the PRIMARY alpha)
  Stage 3:                ⁸Be(*)             (the recoil nucleus, itself unbound)
  Stage 4:                ⁸Be(*) →  α + α     (breaks up into two SECONDARY alphas)
```

Net result: **3 alphas** sharing the ~8.7 MeV release -- but they are emitted in
**two distinct steps**, so they do not come out with equal energies.

Stage 2/3 actually has two competing branches, depending on which state of ⁸Be
is left behind:

- **α₁ branch (~90%):** `¹²C* → α₁ + ⁸Be*(2⁺, 3.03 MeV) → α₁ + 2α`
- **α₀ branch (~10%):** `¹²C* → α₀ + ⁸Be(0⁺, ground state) → α₀ + 2α`

### Time scales of the intermediates

The intermediate nuclei exist for an extraordinarily short time -- set by their
quantum level width via `τ = ℏ / Γ`:

| Intermediate | Decays to | Width Γ | Lifetime τ = ℏ/Γ |
|---|---|---|---|
| **¹²C\*** (compound nucleus, ~16.6 MeV) | α + ⁸Be | ~0.3 MeV | **~10⁻²¹ – 10⁻¹⁸ s** |
| **⁸Be\*** (2⁺, 3.03 MeV) | 2α | ~1.5 MeV | **~4×10⁻²² s** |
| **⁸Be** (0⁺ ground state) | 2α | 5.57 eV | **~8×10⁻¹⁷ s** |

Even the longest-lived of these (⁸Be ground state, ~10⁻¹⁶ s) decays about
50,000× faster than the shortest simulation timestep (HB11's `dt = 5 ps`), and
travels only a fraction of a nanometre before breaking up. **So the simulator
does not transport ¹²C\* or ⁸Be as particles at all** -- treating the reaction
as instantaneous (`p + ¹¹B → 3α`) is fully justified, not a shortcut. What the
simulator *does* keep is the kinematic fingerprint those stages leave on the
alphas.

### The alpha energy distribution

Because the primary alpha (Stage 2) and the two secondary alphas (Stage 4) are
born from different two-body decays, they populate different energy ranges. The
aggregate per-alpha spectrum is modeled as a **weighted sum of Gaussians**, one
per emitted-alpha population:

```
f(E) = Σ_k  w_k · N(E ; μ_k, σ_k)
```

| Component (k) | Origin | μ_k [MeV] | σ_k [MeV] | weight w_k |
|---|---|---|---|---|
| α₁ primary    | Stage 2, α₁ branch | 3.76 | 0.30 | 0.90 × 1/3 |
| α₁ secondary  | Stage 4, ⁸Be\*(3.03) breakup | 2.46 | 1.00 | 0.90 × 2/3 |
| α₀ primary    | Stage 2, α₀ branch | 5.70 | 0.30 | 0.10 × 1/3 |
| α₀ secondary  | Stage 4, ⁸Be(g.s.) breakup | 1.43 | 0.50 | 0.10 × 2/3 |

The weights are normalized to 1; the `1/3 : 2/3` split reflects one primary plus
two secondary alphas per reaction. By construction the mean alpha energy is

```
⟨E_α⟩ = Σ_k w_k μ_k ≈ 2.89 MeV    ⇒    3 ⟨E_α⟩ ≈ 8.7 MeV
```

so the total released energy is conserved on average, while individual alphas
range from ~0 to ~6.8 MeV with the well-known broad peak near ~3.8 MeV. This is
implemented in [`physics/processes.py`](physics/processes.py) as
`sample_alpha_energies_J(n, rng)`.

### Why representing the distribution improves fidelity

The whole point of p-¹¹B is **direct energy conversion** of the charged alphas
to electricity (TAE's ICC, HB11's electrostatic collector grid). A direct
converter is an *energy filter*: a decelerating potential `V` turns back any
alpha whose kinetic energy is below `2eV` and collects the rest. If every alpha
had the same energy `8.7/3 ≈ 2.9 MeV`, the converter response would be an
unphysical step function -- all-or-nothing at one grid voltage.

With the real spectrum, the model behaves like the real machine:

- **Energetic primary alphas (~3.8–5.7 MeV)** punch through higher decelerating
  potentials -- so HB11's `Collected` charge and TAE's `ICC sig` keep responding
  as you raise the grid voltage toward 3 MV.
- **Soft ⁸Be-breakup secondaries (~1–2.5 MeV)** are turned back at lower
  voltages, shaping the collection-efficiency-vs-voltage curve.

In short: modeling the 4-stage chain lets the **direct-conversion diagnostics
respond to a voltage sweep the way a real collector would**, which is exactly
the engineering question these reactors are built to answer.

---

## The colored dots (macroparticles)

Every reactor renders live **macroparticles** -- each dot represents a large
swarm of real particles (a "macroparticle weight") so that millions of physical
particles can be visualized with a few thousand dots. The color encodes the
species, and the color key is identical across all three reactors:

| Color | Species | Charge | What it represents |
|-------|---------|--------|--------------------|
| **Red** | Proton (`p`, ¹H) | +1e | The light fuel ion. |
| **Green** | Boron-11 (`B`, ¹¹B) | +5e | The heavy fuel ion (Z = 5 -- the big Bremsstrahlung driver). |
| **Yellow** | Alpha (`α`, ⁴He) | +2e | Fusion *product*. Each p-¹¹B reaction makes 3 alphas sharing 8.7 MeV. |
| **Blue** | Electron (`e`) | −1e | Neutralizing electrons; their temperature `T_e` sets the radiation losses. |

Reading the motion:
- **Red + Green** dots are the reacting fuel. Where they overlap densely and are
  hot, fusion happens.
- **Yellow** dots *appear over time* -- they are born from fusion events and then
  stream toward a collector (TAE/HB11) or out of the pinch (LPP). Watching yellow
  accumulate is watching the reactor produce energy.
- **Blue** dots track the electron cloud. In the aneutronic concepts the whole
  game is keeping the blue population *colder* than the fuel ions.

The bright **cyan/white shapes** are not particles -- they are the **solid
conductor structures** (walls, electrodes, grids, targets), drawn as
high-contrast overlays and labeled with text.

> The yellow alphas are **not** monoenergetic -- they are sampled from the real
> p-¹¹B energy spectrum produced by the 4-stage decay chain described at the top
> of this guide. That is why the direct-conversion diagnostics (TAE `ICC sig`,
> HB11 `Collected` charge) respond realistically to a grid-voltage sweep.

---

## Universal output measurements (right-hand diagnostic panel)

All three reactors report the same coupled core-process equations, evaluated
every timestep. These feed the three linked real-time plots and the "Live
Readout" text box on the left.

1. **Ion / Electron Temperature** (`T_i`, `T_e`, in keV) -- the top plot.
   The central tension of p-¹¹B: ion temperature must reach ~150-300 keV for
   fusion, while electron temperature should stay low to limit radiation.

2. **Core Power Balance** (W/m³, log scale) -- the middle plot:
   - `P_fusion = n_p n_B ⟨σv⟩ E_f` with `E_f = 8.7 MeV` (yellow).
   - `P_Brems` = relativistic Bremsstrahlung radiation loss (pink):
     `1.57e-40 · Z_eff² · n_e² · √T_e · (1 + 1.71 T_e/m_e c²)`.
   - `P_cond` = conductive/transport energy loss `3 n_e T_e / τ_E` (green).

3. **Net Gain `Q`** (log scale) -- the bottom plot:
   `Q = P_fusion / (P_Brems + P_cond)`. The dashed line marks `Q = 1`
   (scientific breakeven). For thermal p-¹¹B this sits stubbornly below 1 --
   that is the famous **Rider limit**, and it is *supposed* to be hard.

Each reactor also adds a couple of **machine-specific readouts** in the Live
Readout box (described per reactor below).

---

## 1. TAE FRC — beam-driven Field-Reversed Configuration

![TAE FRC](docs/tae_frc.png)

The label **TAE FRC** in the reactor menu names one **physics module**, not a
complete fusion power plant. It models the **plasma-core mechanism** that
[TAE Technologies](https://tae.com) is developing: a **field-reversed configuration
(FRC)** in a linear vessel, sustained primarily by **neutral beam injection (NBI)**,
with **hydrogen–boron (p–¹¹B)** as the intended fuel. The other menu entries (HB11
Laser, LPP DPF) are different reactor concepts; only this one follows TAE’s FRC
program.

**What this is faithful to**

| Included (aligned with TAE’s public FRC story) | Omitted or folded into scalars |
|------------------------------------------------|--------------------------------|
| Self-organized FRC topology; `B_z` reverses on the midplane | End **formation** sections, divertor tanks, vacuum plumbing |
| **NBI-only sustainment** as the hold mechanism (Norm milestone, 2025) | Exact Norm / Norman CAD layout and beam count |
| **p** via tangential NBI; **¹¹B** fueled separately in the core | Powder dropper hardware, guide tubes, cooling details |
| **ICC** concept: alphas on open field lines → segmented collectors | Full **steam + direct-conversion** power train, grid export |
| Shot script: arm → gas fill → ramp → formation → NBI → flat-top → quiescent | Commercial **Da Vinci**-class plant accounting |

So: call it **TAE’s beam-driven FRC plasma core** (menu: **TAE FRC**). It is
**current-technology direction** from a real company, not a generic toy FRC and not
a full “TAE reactor” in one box.

### Where this sits in TAE’s program

TAE’s approach is an FRC — a compact, linear alternative to a tokamak — in which
the plasma forms a rotating structure whose internal field **reverses** relative to
the applied field (often described as a “smoke ring”). The company’s near-term fuel
target is **p–¹¹B** (aneutronic; ash is alphas).

Public milestones relevant to *this* simulator:

- **Norm** (2025): reported **NBI-only** formation, heating, and sustainment of an
  FRC without the older end **collision / formation** sections — a step TAE describes
  as simplifying the machine and cost path toward commercial plants
  ([*Nature Communications*, April 2025](https://doi.org/10.1038/s41467-025-58849-5)).
- **Earlier machines (e.g. Norman)**: used more elaborate end formation; still
  beam-driven FRC physics, but not the same simplified layout Norm advertises.
- **Later plants (Copernicus, Da Vinci — names from TAE’s roadmap)**: aim at net
  energy and grid power with **thermal** wall recovery plus **direct alpha**
  conversion at the ends. Those plants are **not** simulated here.

**Norm is a physics experiment**, not an electrical generator. TAE has **not**
reported wall-plug `Q ≥ 1` on Norm. This GUI adds a **proposed** p–¹¹B operating
point where modeled **`Q_sys` can exceed 1** when you **Optimize** — that explores
fuel and recovery physics, not measured Norm performance.

### Two fuel paths (why only protons use the beam lines)

p–¹¹B needs **both** hydrogen and boron-11, but TAE’s engineering separates the
injection paths (boron is far heavier than hydrogen and is not practical through the
same NBI accelerators):

| Species | In TAE’s FRC approach | In this simulator |
|---------|----------------------|-----------------|
| **Protons (`p`)** | **Neutral beam injection** from the machine flanks: accelerate ions, neutralize so the beam crosses external fields, inject **tangentially**, re-ionize in the hot core; beams supply **heat, rotation, and current drive** to sustain the FRC. | Red macroparticles from **−x**; **`nbi_heat` / flat-top** enable injection; **NBI Current** sets beam energy and rate. |
| **Boron (`¹¹B`)** | Introduced into the **core** (gas/powder injection separate from NBI), where it ionizes in the hot plasma. | Green macroparticles in the core; **`gas_fill`** (“fuel inventory rising”) represents **inventory build-up**, not a modeled dropper geometry. |

In the view: **red from the left = NBI protons**; **green in the midplane = boron
population**; fusion is modeled where they overlap in the FRC core.

### Power plant vs this panel

A full TAE commercial design is described as harvesting energy two ways: **thermal**
conversion from radiation on the walls (steam plant) and **direct conversion** of
fast alphas leaving along open field lines (**inverse cyclotron converter**, ICC).
**This module keeps only the ICC thread** in the 2D slice:

- Yellow alphas drift **+x** to **segmented collectors** on the right boundary.
- **ICC Coupling** is **`η_ICC`**: fraction of **`P_fusion`** booked as electricity in
  the 0D balance.
- Wall X-rays and steam turbines are **not** animated; bremsstrahlung appears in
  **`P_Brems`**, not a second turbine loop.

That keeps the teaching focus on **beam sustainment + p–¹¹B + ICC** — one faithful
slice of TAE’s FRC technology, not the whole plant.

### Physical architecture being modeled

This reactor is a **2D axis-aligned slice** through a cylindrical FRC machine —
the horizontal axis **`x`** is the machine (axial) direction; the vertical axis
**`y`** is the radial-like coordinate across the **field-reversal plane** (an
*r–z* meridian collapsed to *x–y* for visualization).

| Element | Model |
|---------|--------|
| **Domain** | 1.2 m × 0.8 m window (`x ∈ [−0.6, +0.6] m`, `y ∈ [−0.4, +0.4] m`), 181×121 cells |
| **Conducting wall** | Thin grounded shell on the top, bottom, and left boundaries (cyan lines) |
| **ICC collector** | **8 segmented electrodes** on the **+x** end wall — alphas are absorbed here |
| **FRC core** | Interior plasma; field reverses on the midplane `y ≈ 0` |
| **Timestep** | 2 ns (flat-top holds ~25 µs simulated ≈ 12 ms wall time at normal speed) |

During a shot the coil ramp is modeled by a dimensionless scale **`b_scale(t)`**
(0 at Arm → 1 at flat-top) that multiplies the slider **`B0`**:

\[
B_z(x,y,t) = B_0 \cdot b_{\mathrm{scale}}(t) \cdot \tanh\!\left(\frac{y}{y_s}\right),
\qquad y_s = 0.12\ \mathrm{m}
\]

The colormap is **`B_z`**: yellow/positive at the top, purple/negative at the
bottom, with the **field-reversal plane (`B_z = 0`)** through the centre. Macroparticle
positions sample a **`sech²(y/y_s)`** density profile (implemented as a Gaussian
with σ ≈ `y_s`).

**TAE-specific hardware narrative in this slice:**
- **Neutral Beam Injection (NBI)** enters from the **−x** side and deposits **MeV-class
  fast protons** (red dots with a narrow +x velocity cone).
- Fusion **alphas (yellow)** are born in the core and stream **+x** toward the
  **Inverse Cyclotron Converter (ICC)**.
- At the ICC, alphas crossing segmented electrodes induce an **AC pickup signal**
  (`ICC sig` in the readout) — a stand-in for **direct conversion** of charged
  fusion-product energy to electricity (no steam cycle, no neutrons).

The simulator couples a **2D PIC macroparticle view** (what you see bouncing) to a
**0D power-balance model** (what drives the `Q_net` plot and optimizer). The two
are intentionally aligned but not yet fully self-consistent in every detail (e.g.
ICC recovery is counted in 0D before every alpha macroparticle reaches the collector).

### Control inputs (sliders)

| Slider | Range | Default | Effect |
|--------|-------|---------|--------|
| **NBI Current** | 0–120 A | 40 A | Beam current (normalized). Sets beam energy, fast-ion fraction, **`P_NBI`**, and PIC injection rate. |
| **Background B0** | 0.1–5.0 T | 1.5 T | Peak `\|B_z\|` at flat-top (`b_scale = 1`). Enters **`τ_E`**, bulk **`T_i`**, and core density. |
| **ICC Coupling** | 0.50–0.95 | 0.85 | **`η_ICC`**: fraction of fusion power recovered as electricity at the collector. |

### 2D particle dynamics (PIC slice)

Macroparticles for **p**, **¹¹B**, **e⁻**, and **α** are advanced each sub-step with
a **Boris push** in the local **`B_z(y)`** (no in-plane electric field during flat-top).

**Boundaries**
- **Radial walls** (`y` limits): specular reflection of `v_y`.
- **−x wall**: specular reflection of `v_x`.
- **+x wall**: fuel ions reflect; **alphas are collected** when `x` reaches the ICC plane
  (`x_ICC ≈ x_max − 0.04 m`), incrementing **`ICC sig ∝ Σ|v_x|`** of collected alphas.

**NBI injection** (during `nbi_heat` and flat-top, when `nbi_scale > 0`):

Beam energy from the slider (matches the 0D model):

\[
E_{\mathrm{beam\,[keV]}} = 250 + 320\left(\frac{I_{\mathrm{NBI}}}{120}\right)^{0.85}
\]

Protons spawn at the left edge with **`v_x = √(2 E_beam / m_p)`** and a small transverse
spread (`σ_v ≈ 0.08 v_x`). Injection rate scales with **`I_NBI`**.

**Fusion alphas in PIC** spawn at a rate tied to **`P_fusion`**, with kinetic energies
sampled from the **p–¹¹B sequential-decay spectrum** (alpha0/alpha1 branches through
¹²C* and ⁸Be — see *Alpha spectrum* below), launched in a narrow forward (+x) cone.

### 0D plasma state (flat-top scalars)

During flat-top the bulk scalars relax toward:

\[
T_{i,\mathrm{target}} = 40 + 0.35\,E_{\mathrm{beam\,[keV]}} + 18\,B_0\ \ \mathrm{[keV]}
\]

\[
T_{e,\mathrm{target}} = \min\!\bigl(12 + 0.04\,T_i,\ 0.18\,T_i\bigr)\ \ \mathrm{[keV]}
\]

\[
n_e = 3.0\times10^{20}\left(0.55 + 0.45\,\frac{B_0}{5}\right)\ \ \mathrm{m^{-3}}
\]

Fuel fractions: **`n_p = 0.55 n_e · (1 + 0.08 f_beam)`**, **`n_B = 0.09 n_e`**, with

\[
f_{\mathrm{beam}} = \min\!\left(0.72,\ 0.10 + 0.62\,\frac{I_{\mathrm{NBI}}}{120}\right)
\]

(fraction of protons in the non-thermal beam population used by the power balance).

### Power balance and gain (TAE-specific)

The **`Q_net`** plot and **Optimize** use **system gain** for TAE:

\[
\boxed{
Q_{\mathrm{sys}} =
\frac{P_{\mathrm{ICC}}}{P_{\mathrm{NBI}} + P_{\mathrm{Brems}} + P_{\mathrm{transport}}}
}
\]

\[
Q_{\mathrm{plasma}} =
\frac{P_{\mathrm{fusion}}}{P_{\mathrm{Brems}} + P_{\mathrm{transport}}}
\]

(`Q_plasma` is shown in the Live Readout; HB11/LPP use `Q_plasma`-style gain only.)

#### Beam-driven sustainment (required — not a slider floor)

TAE's Norm result is **NBI-only FRC formation**: the reversed field is **created and
held by beam-driven current**, not by external coils alone. The model captures this
with a sustainment fraction **`S(I_NBI, B0) ∈ [0, 1]`**:

\[
S = \underbrace{\mathrm{smoothstep}\!\left(\frac{I_{\mathrm{NBI}} - 30\ \mathrm{A}}{25\ \mathrm{A}}\right)}_{\text{beam holds reversal}}
\times \underbrace{\left(0.70 + 0.30\,\frac{B_0}{5\ \mathrm{T}}\right)}_{\text{B₀ assists confinement once FRC exists}}
\]

**`B₀` cannot substitute for beams** — if **`I_NBI < ~30 A`**, **`S → 0`**: the FRC
does not maintain reversal regardless of field strength.

When **`S`** is low:
- **`τ_E`** collapses (**`∝ S²`**) — confinement time shortens  
- **End/transport losses** rise (**`∝ 1 + 12(1−S)²`**) — open field lines, tilt  
- **Beam-target fusion** scales with **`S`** (overlap + trapping)  
- **Thermal fusion tail** scales with **`S²`** (no free fusion from a cold/decaying FRC)  
- Bulk **`T_i`**, **`n_e`**, and PIC **`B_z` amplitude** scale with **`S`**

There is **no hard minimum current** — low NBI is legal but **self-penalizing**. During the
shot, **`nbi_scale(t)`** ramps from 0 → 1 over the **NBI on** phase; all sustainment,
fusion, **`P_NBI`**, and **`T_i`** scale with **`S × nbi_scale(t)`** so **`Q_sys` rises
smoothly** rather than stepping at phase boundaries.

The optimizer therefore moves to **~55–90 A** (full sustainment) rather than minimizing
beam cost at ~10 A.

**Two different Q metrics** (both plotted on the bottom-right chart):
- **`Q_sys` (solid white)** — plant gain: **`η_ICC · P_fusion / (P_NBI + losses)`**. This
  is what must cross 1 for net electricity; it stays **~1.5–1.7** at optimum because
  **`P_NBI`** is in the denominator.
- **`Q_plasma` (dashed yellow)** — fusion physics only: **`P_fusion / losses`**. This can
  reach **10+** when **`T_i`** is hot — it is *not* breakeven for the wall plug.

#### Fusion power

**Beam–target channel** (dominant — uses beam energy, not bulk `T_i`):

\[
P_{\mathrm{beam}} =
\mathcal{E}_{\mathrm BT}\;
n_{\mathrm{beam}}\,n_B\;
\langle\sigma v\rangle(E_{\mathrm{beam}})\;
E_f,
\qquad
n_{\mathrm{beam}} = f_{\mathrm{beam}}\,n_p,
\qquad
\mathcal{E}_{\mathrm BT} = 4.5
\]

**Thermal tail** (small Maxwellian contribution from the slow ion population):

\[
P_{\mathrm{thermal}} =
0.12\;
n_{p,\mathrm{thermal}}\,n_B\;
\langle\sigma v\rangle(T_{i,\mathrm{thermal}})\;
E_f
\]

\[
P_{\mathrm{fusion}} = P_{\mathrm{beam}} + P_{\mathrm{thermal}},
\qquad
E_f = 8.7\ \mathrm{MeV\ per\ reaction}
\]

Reactivity **`⟨σv⟩`** is a log-parabola fit peaking near **300 keV**:

\[
\log_{10}\langle\sigma v\rangle =
-21.5 - 2.0\left[\log_{10} T - \log_{10} 300\right]^2
\quad (T\ \mathrm{in\ keV})
\]

#### ICC recovery (output)

p–¹¹B releases essentially all energy in **three charged alphas** (no neutrons):

\[
P_{\mathrm{ICC}} = \eta_{\mathrm{ICC}}\,P_{\mathrm{fusion}}
\]

#### NBI input

\[
P_{\mathrm{NBI}} =
\frac{1.35\times10^{5}\,\left(I_{\mathrm{NBI}}/120\right)^{1.35}\,\left(E_{\mathrm{beam\,[keV]}}/400\right)}
{V_{\mathrm{plasma}}},
\qquad
V_{\mathrm{plasma}} \approx \pi y_s^2 L_x \cdot 0.85 \approx 0.05\ \mathrm{m^3}
\]

#### Bremsstrahlung (relativistic)

\[
P_{\mathrm{Brems}} =
1.57\times10^{-40}\,Z_{\mathrm{eff}}^2\,n_e^2\,\sqrt{T_e}\,
\left(1 + 1.71\,\frac{T_e}{m_e c^2}\right)
\]

with **`Z_eff = (n_p + 25 n_B) / n_e`**. Low **`T_e`** (decoupled from beam-heated ions)
keeps this term small — the intended Rider workaround.

#### Transport (thermal populations only)

Fast beam ions are **excluded** from the loss inventory (they fuse before equilibrating):

\[
P_{\mathrm{transport}} =
\frac{\frac{3}{2}\,k_B\!\left(n_{e,\mathrm{loss}} T_e + n_{i,\mathrm{loss}} T_{i,\mathrm{thermal}}\right)}
{\tau_E}
\]

with **`n_e,loss = min(n_e, 4×10¹⁹ m⁻³)`**, **`n_i,loss = min(n_p,thermal, 0.55×4×10¹⁹)`**,
and **`T_i,thermal = min(T_i, 0.22 E_beam + 15 keV)`**.

Energy confinement time (FRC-scaled, grows with **`B0`** and NBI sustainment):

\[
\tau_E =
6.0\times10^{-3}\left(\frac{B_0}{1.5}\right)^{2.4}
\left(1 + 0.75\,\frac{I_{\mathrm{NBI}}}{120}\right)\times 18\ \ \mathrm{s}
\]

#### Alpha spectrum (PIC birth energies)

Each fusion event produces **three alphas** via sequential decay:

| Branch | Weight | Primary α | Secondaries (×2) |
|--------|--------|-----------|------------------|
| **alpha1** | ~90% | ~3.76 MeV | broad ~2.46 MeV (⁸Be* breakup) |
| **alpha0** | ~10% | ~5.70 MeV | ~1.43 MeV (⁸Be ground state) |

Macroparticle alphas draw from this four-component mixture; total kinetic energy
averages **~8.7 MeV per reaction**.

#### ICC AC signal (readout)

\[
\mathrm{ICC\ sig} \leftarrow 0.97\,\mathrm{ICC\ sig} + 0.01\sin\phi,
\qquad
\frac{d\phi}{dt} = 2\pi\left(10^6 + 2\times10^4\,I_{\mathrm{NBI}}\right)
\]

plus increments proportional to collected alpha **`|v_x|`**. Units are arbitrary —
it is a qualitative direct-conversion waveform, not a calibrated MW readout.

### What the dots do

Red / green / blue macroparticles **gyrate** in **`B_z`** (Boris pusher), concentrated
near the midplane. NBI continuously adds **fast red protons** from the left. Yellow
alphas are born near the core and drift **+x**; many are collected at the ICC segments.

**Compile** captures **multiple simulation frames per phase** (not one still image),
then stretches them to match narration timing — so each **Step** segment should show
**particle motion** within that callout after a fresh **Compile**. If motion looks
frozen, re-**Compile** (old disk cache may still hold single-frame runs).

### Machine-specific readout (Live Readout extras)

| Field | Meaning |
|-------|---------|
| **`Sustain`** | Beam-driven FRC hold fraction **`S(I_NBI, B0)`** — see sustainment section |
| **`P_NBI`** | Modeled beam input power density [W/m³] |
| **`P_ICC`** | **`η_ICC · P_fusion`** — recovered output power density |
| **`Q_plasma`** | Fusion vs radiation + transport (no NBI/ICC accounting) |
| **`Q_sys`** | Same as **`Q_net`** plot for TAE |
| **`ICC sig`** | AC pickup from alphas crossing ICC segments |

### On-screen HUD and MP4 export

The plot shows a **bold black frame counter** (top-left of the canvas):
**`Frame N [FF×35 …]`** during the pre-flat-top countdown, **`[FF×10 …]`** during
the long flat-top hold, **`[FF×4 …]`** during ramp-down, then **`[1× …]`** for any
1× segments. Use it to see when the GUI is compressing sim time vs running in real time.
advancing 35× more simulation time per tick.

**Rec Start** / **Rec Save**: captures the **spatial canvas plus the three
right-hand graphs** (temperature, power balance, Q). Control panel is excluded.
The saved MP4 uses the same **narration-first** mix as **Compile** (cached
ChatTTS, each phase held for at least ``speech + 1.5 s``, white **subtitles** on
export). Reactor bed is **2× louder**, ducked during voice. Set
``PB11_SKIP_NARRATION=1`` for bed-only export. Requires **ffmpeg** on PATH.

**Recommended button sequence** (presentation / MP4):

1. **Optimize** (optional; cached on disk after first run)
2. **Compile** (wait for “Compiled…” or “Restored cached compile…” in the status bar)
3. **Rec Start**
4. **Play** or **Step** through numbered segments (arming → countdown → flat-top → quiescent)
5. **Rec Save** → choose path (progress dialog during encode/mux)

Start **Rec Start** before **Play** so the MP4 includes the full arming segment. If you
only need to review (no file), skip **Rec Start** / **Rec Save** and use **Compile**
→ **Step** through each `seq/total` callout.

### Operational sequence (compiled shot segments)

Each numbered step in **Play** / **Step** is one narration segment (see status bar
`1/N … N/N`). The table below is the **TAE FRC** script order.

| Phase | Sim duration (typical) | What happens |
|-------|------------------------|--------------|
| Armed | (held on screen) | Vacuum pumped, gas puffed, coils at standby; weak **`B_z`**. |
| Gas fill | 0.8 µs | Fuel inventory rises (boron + hydrogen inventory before main heating). |
| Coil ramp | 2.0 µs | **`b_scale → 1`**, **`B_z`** rises. |
| FRC formation | 3.0 µs | Hot plasma macroparticles seeded; FRC topology appears. |
| NBI on | 4.0 µs | **`nbi_scale → 1`**, tangential beam injection begins. |
| **Flat-top** | 25 µs | Full discharge; fusion, alphas to ICC, diagnostics live. |
| Ramp-down | 4 µs | Beams and field fall. |
| Quiescent | (held) | Plasma quiescing; shot complete. |

After **quiescent**, a **new Compile** from quiescent uses TAE’s shortened **re-fire**
phase list (no full gas-fill/arming preamble). TAE is the only reactor in this app
that allows that without a fresh arm cycle in the live model.

**Typical cadence:** *Optimize → Compile → Rec Start → Play or Step review → Rec Save*.
Re-**Compile** only if sliders change or you need a quiescent-entry compile.

### Real-world status vs this model

The public **Norm** result is **NBI sustainment of an FRC**; this app layers a
**p–¹¹B performance model** (beam-target fusion, **`η_ICC`**, **`Q_sys`**) on that
same confinement approach. See **Where this sits in TAE’s program** above for how
that differs from a future grid-power plant.

---

## 2. HB11 Laser -- Laser-Driven Block Ignition

![HB11 Laser](docs/hb11_laser.png)

### Physical architecture being modeled
A **2D slice through a spherical reaction chamber**. The outer cyan ring is the
**grounded spherical chamber wall**. A small solid **fuel target** sits at the
center on a thin **target positioner** stalk. Surrounding the target is a
**high-voltage spherical collector grid** -- drawn as the dashed cyan arcs
(it is a *grid*, with gaps, so particles can pass while it holds a high bias).

The displayed field colormap is the **electrostatic potential `Φ`**, obtained by
solving Poisson's equation `∇²Φ = −ρ/ε₀` with the grid pinned at the slider
voltage. You can see the potential well/hill the grid creates.

Two physics processes drive it:
- **Ponderomotive block acceleration:** a localized 2D Gaussian laser pulse
  hits the target and ejects fuel via the ponderomotive force
  `F_p = −(e²/4 m_e ω²) ∇⟨E²⟩`. On picosecond timescales the ions are pushed
  out as a directed "block" before electrons can thermalize -- this is how HB11
  tries to beat the Rider limit (note `T_e` stays much lower than `T_i`).
- **Electrostatic deceleration / direct collection:** outward ions climb the
  grid's potential, are decelerated, and their charge is **collected on the
  grid** as DC current.

### Control inputs (sliders)
| Slider | Range | Default | Effect |
|--------|-------|---------|--------|
| **Laser Intensity** | 1-100 (×10²⁰ W/cm²) | 30 | Strength of the ponderomotive drive. Higher intensity ejects the fuel block harder and drives `T_i` up toward ~300+ keV. |
| **Grid Voltage** | 0-3 MV | 1.5 MV | Bias on the collector grid. Higher voltage decelerates the escaping ions more strongly and collects more charge (the potential field colormap deepens). |

### What the dots do
A cold fuel **block** (red protons + green boron, with blue electrons) starts as
a thin shell on the target. The laser blows it outward; ions decelerate against
the grid potential and are collected. Yellow alphas appear from fusion in the
compressed core and radiate outward. The block is replenished so the run
sustains.

### Machine-specific readout
- **`Collected`** -- total DC charge (Coulombs) accumulated on the collector
  grid. This is the direct-conversion energy harvest.

### Operational sequence (Arm → Fire → quiesce)

**Arm (pre-shot)**  
Chamber pumped, **grid at V_grid**, fresh **fuel target** loaded (cold block on the stalk — green/red/blue dots on the target). No laser power yet.

**Fire countdown**

1. Grid charge — verify high-voltage stand  
2. Laser countdown — `T−3…2…1` (chain armed)  
3. **Main pulse** — ponderomotive block ejection + heating (`Laser Intensity` slider)  
4. Afterglow — plasma cools, collection completes  

**Quiescent**  
Target is spent. **You must Arm again** before the next Fire (new target + pump-down).

**Typical cadence:** *Compile → Rec Start → Arm → Fire → Rec Save* — then **Arm** again (new target) before the next compile/shot.

---

## 3. LPP DPF -- Dense Plasma Focus

![LPP DPF](docs/lpp_dpf.png)

### Physical architecture being modeled
A **2D cross-section perpendicular to the electrode axis** of a coaxial gun.
At the center is the **hollow anode** (inner radius `a`); around the outside is
a ring of **cathode rods** at radius `b` (the cyan blocks). A capacitor bank
discharges across them, forming a **plasma sheath** that is driven inward and
collapses onto the axis as a dense **pinch/focus**.

The displayed field colormap is the **azimuthal magnetic field magnitude**
`|B_θ| = μ₀ I / (2π r)` -- brightest at the center where the current pinches,
falling off as `1/r` outward. Two physics processes drive it:
- **Snowplow sheath dynamics:** the sheath position is integrated from
  `d/dt(M(z) · dz/dt) = μ₀ I(t)² / (4π) · ln(b/a)`, with a ringing RLC current
  `I(t)` set by the capacitor voltage and a swept mass set by the gas pressure.
- **Quantum Magnetic Bremsstrahlung Suppression:** when the pinch field exceeds
  `B_crit = 10⁵ T`, radiation is suppressed by `P_Br · exp(−B/B_crit)`. (At
  realistic DPF currents the field stays well below this extreme threshold, so
  the hook is present but rarely triggers -- as in reality.)

### Control inputs (sliders)
| Slider | Range | Default | Effect |
|--------|-------|---------|--------|
| **Capacitor Voltage** | 10-60 kV | 35 kV | Peak bank current (mega-ampere class). Higher voltage means stronger drive, a tighter/hotter pinch, larger `|B_θ|`, and higher `T_i`. |
| **Gas Pressure** | 0.5-20 Torr | 6 Torr | Fill pressure of the H-B mixture. Sets the swept mass (snowplow inertia) and the plasma density `n_e`. |

### What the dots do
Red/green fuel ions and blue electrons fill the inter-electrode gap and are swept
**inward** with the collapsing sheath (you can watch them migrate toward the axis
as `I(t)` rings up). Ions reflect off the anode surface and are absorbed at the
cathode radius. Yellow alphas are produced in the dense pinch.

### Machine-specific readouts
- **`I(t)`** -- the instantaneous bank current (Amps).
- **`B_pinch`** -- the peak azimuthal field at the collapsing sheath (Tesla).
  Watch this rise as the sheath radius shrinks toward the anode.

### Operational sequence (Arm → Fire → quiesce)

**Arm (pre-shot)**  
Gas fill at slider **Gas Pressure**, **capacitor bank charged** (`I(t) ≈ 0`), cold fuel ions in the gap between anode and cathode.

**Fire countdown**

1. Gas fill — confirm inventory in the coaxial gap  
2. **Trigger** — switch closes; discharge clock starts  
3. Run-down — snowplow sheath accelerates inward (`I(t)` rises)  
4. **Pinch** — focus on axis; `B_pinch` peaks; fusion burst  
5. Disrupt — plasma hits anode; energy release  
6. Recovery — bank depleted, plasma cooling  

**Quiescent**  
Bank empty. **Arm again** (recharge + refill) before the next Fire.

**Typical cadence:** *Compile → Rec Start → Arm → Fire → Rec Save* — then **Arm** again (recharge) before the next shot.

---

## Suggested first experiments

1. **TAE FRC:** Read **§1** (beam-driven FRC plasma core), then **Optimize** → **Compile** → **Step** through `1/N…N/N`. After quiescence, re-**Compile** for the shortened re-fire path. Try **NBI Current** ~60–90 A with a fresh compile.
   Watch `T_i` climb on the top plot and red beam ions stream in from the left.
   Then raise **B0** and note the tighter gyro-orbits and improved confinement.

2. **HB11 Laser:** crank **Laser Intensity** to ~80 and watch the fuel block
   explode outward while `T_i` rockets toward 300 keV but `T_e` stays low (the
   non-thermal advantage). Raise **Grid Voltage** to 3 MV and watch `Collected`
   charge grow in the readout.

3. **LPP DPF:** raise **Capacitor Voltage** to 60 kV and watch `I(t)` and
   `B_pinch` grow and the central `|B_θ|` colormap brighten as the sheath
   collapses. Lower the **Gas Pressure** to make the lighter sheath collapse
   faster.

In every case, glance at the **`Q_net`** plot. Seeing it sit below the `Q = 1`
line is the whole point of p-¹¹B research -- this simulator lets you feel, in
real time, exactly how hard aneutronic breakeven is and which knobs move it.

---

## The **Optimize** button

If you do not yet have intuition for what the sliders do, press **Optimize**.
The optimizer searches *that reactor's own* control space
(whatever sliders it exposes) for the combination that maximizes the
steady-state net gain `Q`, then moves the sliders there for you and reports the
result in the status bar.

How it works:
- It evaluates only the fast **0D plasma-state model** (the same `T_i`/`T_e`,
  density, and power-balance equations that drive the `Q_net` plot), so it does
  **not** need to run the particle simulation -- a full sweep takes ~0.5-5 s.
- It runs a coarse grid sweep over the slider ranges, then a local refinement
  pass around the best point, in a **background thread** so the GUI stays
  responsive (the button shows "Optimizing...").
- The result is applied to the live sliders, so you can immediately watch the
  optimized plasma evolve and then hand-tune from there.

Things you will learn from it:
- **TAE FRC** favors high **B0**, strong **ICC Coupling**, and **NBI ~ 55–90 A**
  (beam sustainment threshold). Below ~30 A the FRC does not hold; the optimizer
  discovers this from physics, not a floor constraint.
- **HB11 Laser** is essentially insensitive to **Grid Voltage** for core `Q`
  (the grid governs *energy collection*, not the fusion balance), and prefers a
  moderate **Laser Intensity** -- a vivid illustration that hotter is not always
  better once Bremsstrahlung scales up.
- **LPP DPF** likes higher **Gas Pressure** (more fuel density) and an
  intermediate **Capacitor Voltage**.

Thermal p-11B remains Rider-limited if you treat it as a Maxwellian plasma with
no beam channel and no ICC recovery. **TAE FRC** is the exception in this
simulator: its `Q_net` is **`Q_sys`**, and the optimizer can push it above 1 when
the proposed physics knobs align. HB11 and LPP still optimize below breakeven
under their respective 0D models.
