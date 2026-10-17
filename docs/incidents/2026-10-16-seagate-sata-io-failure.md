# Incident: Internal Seagate SATA I/O Failure During Format Attempt

**Incident date:** 2026-10-16  
**Host:** `knowhere`, Dell OptiPlex 5050  
**Affected drive:** Seagate `ST1000LM048-2E7172`, serial `WL114BWW`  
**Current status:** ON HOLD — root cause unresolved.

## Summary

An ext4 format attempt on the internal Seagate 1 TB HDD failed with an I/O error while writing and closing filesystem metadata. The kernel recorded queued SATA reads and writes timing out on `ata3`, repeated hard-reset attempts, a reduction in SATA link speed to 3.0 Gbps, then `hardreset failed`, `reset failed, giving up`, and `ata3.00: disable device`. Subsequent `smartctl -x` returned `INQUIRY failed`.

This proves a communication failure at the SATA command/link layer. It does not establish that the disk itself is physically defective. The unresolved possibilities include drive, SATA data/power path, controller/port, link power-management interaction, or kernel/driver behaviour.

## Timeline and evidence

1. Before the incident, the disk had NTFS filesystem UUID `8ABA4CF5BA4CDF77` and was mounted at `/mnt/internal-hdd`.
2. An unmount was run, but an `ntfs-3g` process still held the old partition. The partition table was rewritten. After reboot, the on-disk table was GPT with one full-size partition; `parted` showed a lingering FAT32 signature on that partition.
3. After verifying model and serial, the command `sudo mkfs.ext4 -L knowhere-data /dev/sdd1` was run. It warned that a vfat signature existed; the user confirmed. The command started creating ext4 metadata, then failed with: `mke2fs: Input/output error while writing out and closing file system`.
4. Kernel logs around 07:54:36 on 2026-10-16 showed failed `READ FPDMA QUEUED` and `WRITE FPDMA QUEUED` commands, SATA exception `SError: { CommWake }`, and hard-reset retries.
5. At 07:55:36 the kernel logged `ata3: hardreset failed`, `ata3: reset failed, giving up`, `ata3.00: disable device`, read/write I/O errors on `sdd`, and buffer I/O errors on `sdd1`.
6. `lsblk -f` subsequently showed no recognized filesystem on the Seagate partition. `smartctl -x /dev/sdd` returned `Smartctl open device: /dev/sdd failed: INQUIRY failed`.
7. Controller diagnostics identified PCI device `00:17.0` as Intel 200 Series PCH SATA controller `[8086:a282]`, Dell subsystem `[1028:07a2]`, in AHCI mode using driver `ahci`. The kernel at the time was Ubuntu `7.0.0-38-generic` on Ubuntu 26.04.1 LTS.
8. The affected host `host4` / `ata3` used `med_power_with_dipm` for SATA link power management. Runtime policy was changed to `max_performance` and a host rescan was attempted. This did not recover the device; at 08:11:52 the kernel again logged failed hard reset and detached SCSI device `4:0:0:0`.
9. As of the latest full `lsblk` on 2026-10-17, Linux re-enumerated devices differently. The Seagate is now `/dev/sdc` (serial `WL114BWW`) with `/dev/sdc1` and no recognized filesystem or mountpoint. The previous `/dev/sdd` name now refers to the Kingston SSD. Device letters must not be reused from earlier instructions.

## Last confirmed disk map (2026-10-17)

| Current node | Model | Serial | Filesystem / mount | Handling |
|---|---|---|---|---|
| `/dev/sda` | One Touch w/PW 1 TB | `00000000NACNBMA9` | exFAT UUID `AD95-6C91`, `/mnt/immich` | External Immich source; preserve |
| `/dev/sdb` | Crucial BX500 500 GB | `2239E66F361E` | Ubuntu root, EFI and boot | OS drive; do not format |
| `/dev/sdc` | Seagate ST1000LM048-2E7172 | `WL114BWW` | Partition exists, no recognized filesystem | Affected drive; on hold |
| `/dev/sdd` | Kingston SNVS500G 512 GB | `50026B7684B4198D` | NTFS UUID `D09481079480F0F0`, `/mnt/media` | External data source; preserve |

## Safeguards while on hold

- Do not retry `mkfs`, `wipefs`, `badblocks`, filesystem repair, or other write tests against the Seagate.
- Do not format, repair, rename, or sync over either external source drive.
- Do not assume any `/dev/sdX` letter is stable. Verify model and serial before future maintenance.
- Do not start Immich migration or configure the portable mirror until storage communication is stable and the incident is explicitly resumed.
- No successful ext4 filesystem or `/srv/storage` mount has been confirmed.
- The requested external copy is a future one-way file-level mirror, not RAID1; no mirror job is configured.

## Next diagnostics when resumed

1. Query available kernel packages and inspect relevant release notes/bug reports before changing boot configuration. At the time of the initial checks, only `/boot/vmlinuz-7.0.0-38-generic` was present; a package record for `linux-image-unsigned-7.0.0-30-generic` was marked removed (`rc`), not an installed kernel.
2. When the operator is physically available and a safe maintenance window exists, check SATA data and power connections; if possible test a known-good cable/port. This is a diagnostic option, not a conclusion.
3. If the drive responds again, collect SMART output before further writes.
4. Only after stable communication and a confirmed cause/acceptable risk should ext4 be created and deliberately validated with controlled reads/writes.
5. Continue Immich migration only after primary storage is stable, data capacity is measured, the database dump is restore-tested, and the Windows deployment plus both external datasets remain intact.
