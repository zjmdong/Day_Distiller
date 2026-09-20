# Branch guide / 分支说明

Reviewed **2026-09-20**. This table describes the remote branches and their committed code, not uncommitted work on a developer's machine.

| Branch | Role | Committed code baseline | Where to start |
| :--- | :--- | :--- | :--- |
| [`firmware-production`](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) | Main project and recommended firmware entry point | Firmware identifies as **2.1.0**; HW 2.0 | [Project README](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/README.md), [setup](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/GETTING_STARTED.md) |
| [`desktop-app`](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) | Current desktop companion | Python package **0.3.0**; Windows / Apple Silicon macOS | [Desktop README](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.md) |
| [`firmware-dev`](https://github.com/zjmdong/Day_Distiller/tree/firmware-dev) | Firmware development and integration | Same committed firmware code as the production branch at review (`87a15cc`), with a separate development README | [Development warning](https://github.com/zjmdong/Day_Distiller/blob/firmware-dev/README.md) |
| [`old/usb-link-dev-firmware`](https://github.com/zjmdong/Day_Distiller/tree/old/usb-link-dev-firmware) | Historical USB Link firmware work | Earlier CDC/MSC implementation (`319b7cb`), before current transactional export/settings services | [Snapshot README](https://github.com/zjmdong/Day_Distiller/blob/old/usb-link-dev-firmware/README.md) |
| [`old/windows-dev-client-py`](https://github.com/zjmdong/Day_Distiller/tree/old/windows-dev-client-py) | Historical Windows USB utility | Package **0.1.0** (`fba0cb2`); device/MSC controls, not the current journal application | [Snapshot README](https://github.com/zjmdong/Day_Distiller/blob/old/windows-dev-client-py/README.md) |

The hashes identify the code reviewed before this documentation update. The documentation commits themselves have later hashes.

## Development boundaries

- Use `firmware-production` and `desktop-app` for the current system. `production` is a branch role, not a certification or an endurance-test result.
- Develop firmware changes on `firmware-dev`, preserving protocol and recording compatibility unless intentionally coordinating a change with the desktop implementation.
- Treat `old/*` as historical development reference. Do not assume current bug fixes, modern device settings, export safety or AI features exist there.
- Local branch names that are no longer present on GitHub are not additional public releases. This guide intentionally lists only the five remote branches present at review.
- A future change can make branches diverge again; check the actual commit and `HELLO` capabilities, not just the branch name.

## 中文说明

`firmware-production` 是主入口，`desktop-app` 是当前桌面应用。`firmware-dev` 用于固件开发；截至核对时，远端已提交的固件代码与主分支一致，不能把开发者本地未提交的功能或版本描述成远端已经发布。

两个 `old/*` 分支保留早期 USB 联调与 Windows 工具实现，适合查阅历史方案，不建议直接用于日常记录或作为新用户起点。它们不具有当前主分支的全部事务导出、配置或日志生成能力。

原始硬件资料、个人采集数据、实机日志和本机配置不属于公开文档。公共测试应使用合成或已脱敏的数据。
