# Atmosphere-NX/Atmosphere — Repository Exploration

**Source:** https://github.com/Atmosphere-NX/Atmosphere/tree/master  
**Date reviewed:** 2026-05-14

---

## What It Is

Atmosphère is a custom firmware (CFW) replacement for the Nintendo Switch. It sits between the hardware and the stock OS, patching and replacing system components at every privilege level — from the very first boot stage through the application layer.

---

## Architecture — Five Layered Modules

The firmware is structured as a strict privilege hierarchy:

| Layer | Module | EL / Privilege | Role |
|---|---|---|---|
| 1 | **Fusée** | Bare-metal | First-stage bootloader; validates and patches TrustZone & kernel before they run |
| 2 | **Exosphère** | EL3 (Secure Monitor) | Replaces Nintendo's TrustZone implementation; owns the highest privilege level |
| 3 | **Thermosphère** | EL2 (Hypervisor) | Provides EmuNAND (virtual NAND) via hardware virtualization |
| 4 | **Stratosphère** | EL0/EL1 (System modules) | 20+ custom system services replacing/extending Nintendo's sysmodules |
| 5 | **Troposphère** | Application layer | OS-level patches for final user-facing customization |

---

## Directory Map

```
Atmosphere/
├── fusee/                  # EL3 first-stage bootloader
│   ├── loader_stub/
│   ├── program/
│   └── build_package3.py
├── exosphere/              # Secure Monitor replacement
│   ├── loader_stub/
│   ├── mariko_fatal/       # Fatal handler for Mariko (T214) SoC variant
│   ├── program/
│   ├── sdmmc_test/
│   └── warmboot/           # Resume-from-sleep handler
├── thermosphere/           # EL2 / EmuNAND hypervisor
├── mesosphere/             # Custom kernel
│   ├── kernel/
│   └── kernel_ldr/
├── stratosphere/           # ~22 system service replacements
│   ├── ams_mitm/           # MITM interceptor (core patch engine)
│   ├── loader/             # Program loader with code-patching (IPS/cheat support)
│   ├── sm/                 # Service manager
│   ├── pm/                 # Process manager
│   ├── fs/                 # Filesystem service
│   ├── ncm/                # Content manager
│   ├── ro/                 # Read-only / NRO loader
│   ├── spl/                # Secure processor loader
│   ├── dmnt/               # Debug monitor
│   ├── dmnt.gen2/          # Debug monitor v2 (cheat engine)
│   ├── fatal/              # Fatal error handler
│   ├── creport/            # Crash reporter
│   ├── erpt/               # Error report service
│   ├── boot/               # Boot sysmodule
│   ├── boot2/              # Secondary boot stage sysmodule
│   ├── htc/                # USB debugging (host–target comms)
│   ├── cs/                 # Capability / certificate service
│   ├── jpegdec/            # JPEG decoder
│   ├── pgl/                # Program general library service
│   ├── memlet/             # Memory management
│   ├── LogManager/         # Log management
│   └── TioServer/          # TIO server
├── emummc/                 # Virtual NAND kernel module
├── libraries/              # Shared libraries
│   ├── libvapours/         # Core utilities & abstractions (used by everything)
│   ├── libexosphere/       # Low-level OS-dev primitives
│   ├── libmesosphere/      # Kernel-level APIs
│   └── libstratosphere/    # High-level application framework
├── mesosphere/
├── utilities/              # Build/release tooling
├── tests/
├── docs/
└── config_templates/       # Example INI configs for end users
```

---

## Technology Stack

| Language | Share | Usage |
|---|---|---|
| C++ | ~83% | Almost all system modules and libraries |
| C | ~14% | Low-level drivers, kernel-adjacent code |
| Assembly | ~1% | CPU init, context switch, MMU setup |
| Makefile | ~1.5% | Build system |
| Python | <1% | Build scripts (`build_package3.py`, `build_mesosphere.py`) |

Build toolchain: **devkitPro / devkitA64** (AArch64 bare-metal GCC toolchain).

---

## Library Layer Detail

`libraries/` is the internal SDK that all modules share:

- **libvapours** — Foundation: result codes, scope guards, constexpr utilities, compile-time checks. Every other component depends on this.
- **libexosphere** — Hardware abstraction for EL3: crypto (AES, RSA, SHA), fuses, MMIO helpers.
- **libmesosphere** — Kernel object model: KProcess, KThread, KMemoryManager, IPC primitives.
- **libstratosphere** — High-level framework: IPC server infrastructure, service registration, filesystem abstraction, HOS API wrappers.

---

## Notable Design Decisions

1. **MITM architecture** — `ams_mitm` intercepts IPC calls to Nintendo's services before they reach the real implementation, allowing selective override without fully replacing every service.

2. **Dual-NAND (EmuNAND)** — Thermosphère virtualises NAND at EL2 so users can run a fully isolated "emulated" Switch OS from an SD partition, leaving the real NAND untouched.

3. **Cheat engine in dmnt.gen2** — The debug monitor exposes a cheat-script runtime (conditional writes, arithmetic opcodes) that runs at game-memory level via debug attach.

4. **SoC variants handled** — Codebase explicitly separates Erista (T210, original Switch) and Mariko (T214, Switch v2 / Lite / OLED) paths (e.g., `mariko_fatal/`, conditional fuse reads).

5. **Zero build-time Nintendo SDK dependency** — Everything reverse-engineered; the shared libraries reimplement the Nintendo OS API surface from scratch.

---

## Key Stats

| Metric | Value |
|---|---|
| Stars | ~19 000 |
| Commits | 4 310+ |
| Releases | 91 |
| License | GPL-2.0 (Nintendo exempt → 0BSD) |
| Core maintainers | SciresM, TuxSH, hexkyz, fincs |

---

## Summary

Atmosphère is a full-stack firmware replacement covering every privilege level of the Nintendo Switch (EL3 → EL2 → EL1 → EL0). Its layered architecture, clean library separation (`libvapours` → `libexosphere` → `libmesosphere` → `libstratosphere`), and MITM-based extensibility model make it a well-engineered example of embedded systems / OS-level reverse engineering in C++.
