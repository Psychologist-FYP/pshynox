# NVIDIA diagnosis for this laptop (4 November 2026)

GPU acceleration is optional: note inference already ran using CPU.
Read-only checks found GTX 1650 Mobile / Max-Q (4096 MiB), Fedora 44,
kernel 7.2.13-200.fc44.x86_64, NVIDIA akmod/kmod and CUDA user libraries 615.78.08.
`modinfo nvidia` finds a signed module for the running kernel. Secure Boot is enabled.
The kernel repeatedly rejects module loading with **unavailable key**; Nouveau owns
the device. This strongly indicates the module signing key needs enrollment, rather
than missing GPU hardware. The kernel command line already blacklists nouveau/nova_core.
No system settings, drivers or keys were changed by this task.

Use the installed package's authoritative instructions:
`/usr/share/doc/akmods/README.secureboot`. The certificate directory was inaccessible
without privilege, so its enrollment could not be independently checked here.

Run these yourself in a terminal (not while expecting uninterrupted desktop work):

```bash
sudo mokutil --test-key /etc/pki/akmods/certs/public_key.der
sudo mokutil --import /etc/pki/akmods/certs/public_key.der
```

If the first command says already enrolled, stop and compare the certificate subject
with `modinfo -F signer nvidia`; a different signing key requires investigation. If
certificate is missing, do not blindly replace keys: inspect the akmods directory and
README before generating/rebuilding. Never disclose a private signing key.

The import command requests a temporary enrollment password. Save your work and reboot.
In the blue MOK screen choose **Enroll MOK → Continue → Yes**, enter that password
(the firmware keyboard uses QWERTY), and reboot again. Then check:

```bash
sudo mokutil --test-key /etc/pki/akmods/certs/public_key.der
nvidia-smi
lspci -nnk -s 01:00.0
```

Expected: enrolled certificate, NVIDIA driver in use, and working `nvidia-smi`.
If it still fails, collect `journalctl -b -k` key/NVIDIA errors and the signer. Do not
unload Nouveau from a running graphical desktop. Initramfs repair or module rebuilding
may be needed, but the current evidence does not justify a blind reinstall.

Secure Boot module enrollment is also described by
[Fedora's NVIDIA Secure Boot feature documentation](https://fedoraproject.org/wiki/Changes/NvidiaInstallationWithSecureboot).
A working driver alone does not turn the tested CPU llama.cpp build into a CUDA build;
see NOTE_GENERATION.md for the explicit rebuild/configuration path.

### Update: 4 November 2026

The driver is now accessible: `nvidia-smi` reports NVIDIA 615.78.08 and the GTX 1650
with 4096 MiB VRAM. The restricted tool sandbox could not access it, but the same
command outside the sandbox succeeded. This resolves the earlier driver observation.
The note runtime is still the existing CPU build; `nvcc` was not found. No CUDA build,
GPU offload configuration or system driver changes were performed in the UI fix.

The CUDA note runtime is now installed locally as official llama.cpp b12079 (CUDA 12.8).
Its shared libraries are scoped via `NOTE_LIBRARY_PATH`, and the local `.env` requests
99 GPU layers. A real Qwen load confirmed 37/37 layers on the GTX 1650 with about 3 GiB
VRAM. The earlier "no layers were confirmed" error was caused by default verbosity
hiding the offload summary; explicit verbosity 4 fixes the verification.
