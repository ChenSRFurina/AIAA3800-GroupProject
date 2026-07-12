# VPet 虚拟桌宠模拟器 — 项目结构与功能添加指南

> **版本**: 基于 VPet-Simulator v1.10.6.8 代码分析  
> **仓库**: https://github.com/LorisYounger/VPet  
> **许可证**: Apache License 2.0  
> **技术栈**: C# / .NET 8.0 / WPF / LinePutScript

---

## 目录

1. [项目概述](#1-项目概述)
2. [顶层目录结构](#2-顶层目录结构)
3. [解决方案与项目依赖关系](#3-解决方案与项目依赖关系)
4. [VPet-Simulator.Core — 核心引擎](#4-vpet-simulatorcore--核心引擎)
5. [VPet-Simulator.Windows.Interface — MOD/插件接口层](#5-vpet-simulatorwindowsinterface--mod插件接口层)
6. [VPet-Simulator.Windows — 桌面应用](#6-vpet-simulatorwindows--桌面应用)
7. [VPet-Simulator.Tool — MOD制作工具](#7-vpet-simulatortool--mod制作工具)
8. [VPet.Solution — 设置编辑器与存档查看器](#8-vpetsolution--设置编辑器与存档查看器)
9. [动画系统详解](#9-动画系统详解)
10. [MOD 系统详解](#10-mod-系统详解)
11. [数据格式 — LinePutScript (LPS)](#11-数据格式--lineputscript-lps)
12. [功能添加指南](#12-功能添加指南)
    - [12.1 添加新食物](#121-添加新食物)
    - [12.2 添加新动画](#122-添加新动画)
    - [12.3 添加新行为/互动](#123-添加新行为互动)
    - [12.4 创建代码插件 (Plugin)](#124-创建代码插件-plugin)
    - [12.5 添加新物品类型](#125-添加新物品类型)
    - [12.6 添加新窗口/UI](#126-添加新窗口ui)
    - [12.7 添加新主题](#127-添加新主题)
    - [12.8 添加新语言/翻译](#128-添加新语言翻译)
    - [12.9 添加新宠物模型](#129-添加新宠物模型)
    - [12.10 添加新工作类型](#1210-添加新工作类型)
13. [关键文件速查表](#13-关键文件速查表)
14. [架构决策与设计模式](#14-架构决策与设计模式)
15. [构建与调试](#15-构建与调试)

---

## 1. 项目概述

**VPet (Virtual Pet Simulator / 虚拟桌宠模拟器)** 是一个开源桌面宠物应用，支持在 Steam 上免费获取。宠物会在桌面上行走、吃饭、喝水、工作、睡觉、说话，并响应点击、拖拽和鼠标手势。

### 核心特性

| 特性 | 说明 |
|------|------|
| 动画系统 | 32 种动画类型 × 4 种状态 × 最多 3 段式 (ABC) 动画 |
| MOD 系统 | 支持动画替换、食物/物品扩展、对话文案、代码插件 |
| Steam 创意工坊 | 一键订阅和安装 MOD |
| 代码插件 | 通过 `MainPlugin` 基类开发任意功能扩展 |
| 多语言 | 简体中文、繁体中文、英文、日文 (通过 MOD 扩展) |
| NuGet 嵌入 | `VPet-Simulator.Core` 可嵌入任何 WPF 应用 |
| 多人联机 | Steam 访客桌系统 |

---

## 2. 顶层目录结构

```
VPet/
├── .editorconfig                     # 代码风格配置
├── .gitignore
├── VPet.sln                          # Visual Studio 2022 解决方案
├── LICENSE                           # Apache 2.0
├── README.md / README_en.md / README_ja.md / README_zht.md
├── CONTRIBUTING.md / CONTRIBUTING_en.md / CONTRIBUTING_zht.md
├── Secondary Development Support Documentation.md  # 二次开发支持文档
├── vpeticon.ase / .ico / .png        # 应用图标
│
├── VPet-Simulator.Core/              # 核心库 (可嵌入任何 WPF 应用)
├── VPet-Simulator.Windows/           # 桌面可执行程序 (主游戏)
├── VPet-Simulator.Windows.Interface/ # 插件/MOD 接口层
├── VPet-Simulator.Tool/              # MOD 制作辅助工具
└── VPet.Solution/                    # 独立的设置编辑器与存档查看器
```

---

## 3. 解决方案与项目依赖关系

### 项目一览

| 项目 | 类型 | 目标框架 | 说明 |
|------|------|----------|------|
| `VPet-Simulator.Core` | 类库 | net8.0-windows | 核心引擎：动画、模拟、显示 |
| `VPet-Simulator.Windows` | WinExe | net8.0-windows (x86/x64) | 主桌面应用 |
| `VPet-Simulator.Windows.Interface` | 类库 | net8.0-windows | MOD/插件接口合约 |
| `VPet-Simulator.Tool` | 控制台 | net48 | 动画素材处理工具 |
| `VPet.Solution` | WinExe | net8.0-windows | 设置编辑器与存档查看器 |

### 依赖图

```
VPet-Simulator.Core          (无项目依赖)
        ↑
        ├── VPet-Simulator.Windows.Interface   (引用 Core)
        │         ↑
        ├── VPet-Simulator.Windows              (引用 Core + Interface)
        └── VPet.Solution                       (引用 Core + Interface)

VPet-Simulator.Tool           (独立，无项目依赖，net48)
```

### 关键 NuGet 依赖

| 包名 | 用途 |
|------|------|
| **LinePutScript** / **LinePutScript.Localization.WPF** | 自定义序列化格式，用于配置文件、存档、MOD 元数据 |
| **Panuon.WPF** / **Panuon.WPF.UI** | WPF UI 框架 (主题控件) |
| **SkiaSharp** | 精灵图 (Sprite Sheet) 生成，合并动画帧 |
| **Facepunch.Steamworks** | Steam API 集成 |
| **NAudio** | 音频输入检测 (音乐舞蹈功能) |
| **WpfAnimatedGif** | WPF GIF 动画支持 |

---

## 4. VPet-Simulator.Core — 核心引擎

### 4.1 目录结构

```
VPet-Simulator.Core/
├── Handle/                    # 接口与核心控制
│   ├── GameCore.cs            # 根游戏状态容器 (Controller, Graph, Save, TouchEvents)
│   ├── IGameSave.cs           # 存档接口 (Money, Exp, Level, Strength, ModeType枚举)
│   ├── GameSave.cs            # 简单/后备存档实现
│   ├── IFood.cs               # 食物效果接口
│   ├── IController.cs         # 窗口定位接口 (移动、缩放、屏幕检测)
│   ├── PetLoader.cs           # 宠物模型加载器 (从MOD目录加载动画)
│   ├── Function.cs            # 通用工具函数
│   └── SayInfo.cs             # 对话气泡数据类 (支持流式文本)
│
├── Graph/                     # 图形渲染
│   ├── IGraph.cs              # 动画接口 + TaskControl 播放控制
│   ├── GraphCore.cs           # 动画注册表、配置、缓存管理
│   ├── GraphInfo.cs           # 动画元数据 (名称、类型、段式、状态)
│   ├── GraphHelper.cs         # 工作/移动定义、GraphType 辅助方法
│   ├── PNGAnimation.cs        # 多帧PNG动画 (精灵图方式)
│   ├── FoodAnimation.cs       # 三层复合动画 (前/中/后层，用于进食)
│   └── Picture.cs             # 单帧静态图像动画
│
└── Display/                   # UI 显示组件
    ├── Main.xaml / .xaml.cs   # 核心显示控件 (500x500 Viewbox, 双缓冲)
    ├── MainDisplay.cs         # 动画显示管理 (Display, FindGraph, Run)
    ├── MainLogic.cs           # 模拟循环 (EventTimer, 资源消耗, 状态机)
    ├── MessageBar.xaml/.cs    # 对话气泡
    ├── ToolBar.xaml/.cs       # 右键工具栏
    ├── WorkTimer.xaml/.cs     # 工作计时器
    ├── Theme.xaml             # 主题资源字典
    └── basestyle.xaml         # 基础样式资源字典
```

### 4.2 核心数据流

```
GameCore (根容器)
  │
  ├── IController        → MWController (Windows项目) — 控制窗口移动/缩放/位置
  ├── GraphCore          → 所有动画的注册表
  │     ├── GraphsList   [Name][AnimatType] → List<IGraph>
  │     ├── GraphsName   [GraphType] → Set<Name>
  │     ├── GraphsALL    → 所有IGraph (用于清理)
  │     └── GraphConfig  → 触摸区域、移动、工作定义
  ├── IGameSave          → GameSave_VPet (宠物属性: 金钱/经验/等级/饱腹/口渴/心情/健康/好感)
  └── List<TouchArea>    → 触摸区域 (头部、身体、拖拽)
```

### 4.3 Main 类 — 核心协调器

`Main` 类 (在 `Display/Main.xaml.cs`) 是整个核心的中枢，一个 500×500 的 WPF `ContentControlX`，内含 `Viewbox`：

- **双缓冲渲染**：`PetGrid` + `PetGrid2` 两个 `Decorator` 交替使用，避免闪烁
- **输入处理**：鼠标左键 (区分点击/长按)、拖拽、MouseWow (快速晃鼠标)
- **动画调度**：通过 `MainDisplay.cs` 的各种 `Display*()` 方法切换动画
- **模拟循环**：`EventTimer` 每 15 秒 (可配置) 触发一次 tick，执行资源消耗/恢复和随机行为

### 4.4 触摸系统

`TouchArea` (定义在 `GameCore.cs`)：

- `Locate` (Point) + `Size` → 判定区域
- `IsPress` → 长按 vs 点击
- `DoAction` → 触发时的回调
- 默认配置 5+ 区域：侧边隐藏显示、头部触摸、身体触摸、4 个状态特定的拖拽区域

### 4.5 工作与移动系统

**Work** (`GraphHelper.cs` 内部类):

| 属性 | 说明 |
|------|------|
| `MoneyBase` | 基础收益 |
| `StrengthFood/Drink` | 资源消耗 |
| `Feeling` | 心情消耗 |
| `LevelLimit` | 等级限制 |
| `Time` | 工作时长 (分钟) |
| `FinishBonus` | 完成奖励 |

三种工作类型：`Work` (赚钱)、`Study` (赚经验)、`Play` (恢复心情)

**Move** (`GraphHelper.cs` 内部类)：基于方向的移动，带触发条件判断。支持边界检测和兼容移动过渡。

### 4.6 对话系统

`SayInfo` 类层次结构：

```
SayInfo (抽象基类)
  ├── SayInfoWithOutStream   # 简单文本对话
  └── SayInfoWithStream      # 流式文本 (来自 LLM API)
       ├── Event_Update      # 增量文本更新
       └── Event_Finish      # 完成回调
```

### 4.7 宠物加载器 (PetLoader)

`PetLoader.cs` 扫描 MOD 目录并自动识别动画：

| 识别规则 | 结果 |
|----------|------|
| 文件夹只有一张 PNG | → `Picture` (静态图) |
| 文件夹有多张 PNG | → `PNGAnimation` (多帧动画) |
| 文件夹有 `info.lps` | → 手动映射，支持 `FoodAnimation`/`PNGAnimation`/`Picture` |

`IGraphConvert` 字典可将 LPS 类型字符串映射到加载器委托，插件可扩展。

---

## 5. VPet-Simulator.Windows.Interface — MOD/插件接口层

### 5.1 目录结构

```
VPet-Simulator.Windows.Interface/
├── IMainWindow.cs           # 面向插件的 GOD 接口 (~200行)
├── MainPlugin.cs            # 所有代码插件的抽象基类
├── ISetting.cs              # 设置接口
├── ExtensionFunction.cs     # 扩展方法 (Work, IGameSave, IFood)
├── Resources.cs             # 资源和图像资源管理器
├── ScheduleTask.cs          # 工作排程系统
├── Theme.cs                 # 主题和字体类
├── TalkBox.xaml/.cs         # 对话窗口基类 + ITalkAPI 接口
├── Statistics.cs            # 玩家统计数据
├── ActivityLog.cs           # 活动日志
├── GameSave_v2.cs           # v2 存档格式 (MD5 校验)
├── GameSave_VPet.cs         # 完整存档 (含升级、好感等)
│
└── Mod/                     # MOD 数据模型
    ├── IModInfo.cs           # MOD 元数据接口
    ├── IText.cs              # 文本基类 (标签过滤、变量替换)
    ├── ICheckText.cs         # 条件文本 (等级、心情、金钱范围)
    ├── ClickText.cs          # 点击触发的随机对话 (也实现 IFood)
    ├── SelectText.cs         # 可选择对话选项
    ├── LowText.cs            # 低资源警告文本
    ├── Food.cs               # 食物类 (继承 Item, 实现 IFood)
    ├── Item.cs               # 物品基类 (工厂模式 + UseAction 字典)
    └── Photo.cs              # 照片/图鉴 (含解锁条件)
```

### 5.2 IMainWindow — 插件可用的完整 API

`IMainWindow` 是插件能访问的全部功能入口：

| 类别 | 成员 |
|------|------|
| **宠物状态** | `Core` (GameCore), `Main` (Main), `GameSavesData` |
| **MOD 系统** | `Plugins`, `Foods`, `Pets`, `ImageSources`, `FileSources` |
| **文本系统** | `ClickTexts`, `SelectTexts`, `LowFoodText`, `LowDrinkText` |
| **图鉴** | `Photos`, `ShowGallery()` |
| **商店** | `ShowBetterBuy(FoodType)`, `TakeItem(Food)`, `Event_TakeItem` |
| **窗口** | `ShowInputBox()`, `ShowSetting()`, `Close()`, `Restart()`, `Windows` |
| **设置** | `Set` (ISetting), `SetZoomLevel()`, `Save()`, `LoadDIY()` |
| **MOD 数据** | `DynamicResources` (Dictionary), `ModInfo`, `OnModInfo`, `MODPath` |
| **多人** | `MutiPlayerHandle`, `MutiPlayerStart()`, `IMPWindows` |
| **动画** | `DisplayFoodAnimation()`, `Dispatcher` |
| **物品** | `Items`, `ItemsAdd()`, `Item.Creators` (工厂) |
| **排程** | `ScheduleTask`, `SchedulePackage` |
| **事件** | `Event_NewDay`, `Event_TakeItem` |
| **Steam** | `IsSteamUser`, `SteamID`, `HashCheck` |
| **日志** | `ActivityLogs` (ObservableCollection) |

### 5.3 MainPlugin — 插件生命周期

```csharp
public abstract class MainPlugin
{
    public abstract string PluginName { get; }   // 必须与 MOD 目录名一致
    public IMainWindow MW;                        // 对主窗口的引用

    public virtual void LoadPlugin()  { }         // 游戏数据加载后调用 — 注册事件、添加UI
    public virtual void GameLoaded() { }          // 所有内容加载完毕后调用
    public virtual void EndGame()    { }          // 退出时调用 — 清理资源
    public virtual void Save()       { }          // 存档时调用 — 写入自定义数据
    public virtual void Setting()    { }          // 打开插件设置对话框
    public virtual void LoadDIY()    { }          // 添加自定义工具栏按钮
}
```

### 5.4 食物与物品系统

**Food** (`Mod/Food.cs`)：

```
FoodType 枚举: Food | Star | Meal | Snack | Drink | Functional | Drug | Gift
属性: Exp, Strength, StrengthFood, StrengthDrink, Feeling, Health, Likability
RealPrice → 根据属性自动计算公平价格
IsOverLoad() → 反作弊检查
Graph → 指定进食动画名称
Clone() → 创建可消耗副本
```

**Item** (`Mod/Item.cs`) — 工厂模式 + 使用动作：

```csharp
// 注册自定义物品类型
Item.Creators["Wearable"] = (imw, line) => new Wearable(line);

// 注册使用逻辑
Item.UseAction["Wearable"] = [(imw, item) => { /* 装备逻辑 */ return true; }];
```

物品类型：`Item`, `Food`, `Tool`, `Toy`, `Mail`

### 5.5 文本系统

| 类 | 说明 |
|----|------|
| `IText` | 基础文本，支持 `{name}` `{food}` `{drink}` 等变量替换 |
| `ICheckText` | 条件文本，带 LikeMin/Max, LevelMin/Max, MoneyMin/Max, Mode 等过滤条件 |
| `ClickText` | 点击随机对话，可按 DayTime 和 Working 状态筛选，同时实现 `IFood` 可给予奖励 |
| `SelectText` | 对话选项，支持 Tags 分类和 ToTags 跳转 |
| `LowText` | 低资源警告，分 Strength (S/M/L) 和 Mode (H/L) 级别 |

---

## 6. VPet-Simulator.Windows — 桌面应用

### 6.1 目录结构

```
VPet-Simulator.Windows/
├── App.xaml / .xaml.cs              # 应用入口 — 多存档、异常处理
├── MainWindow.xaml / .xaml.cs       # 主窗口 — 游戏初始化与生命周期
├── MainWindow.cs                    # 主窗口类定义
├── MainWindow_Property.cs           # 主窗口属性 (实现 IMainWindow)
├── PetHelper.xaml / .xaml.cs        # 快速切换宠物
├── mklink.bat                       # 管理员脚本 — 链接 mod 文件夹到构建输出
│
├── Function/                        # 功能性代码
│   ├── CoreMOD.cs                   # MOD 加载器 — 解析 info.lps, 分类加载, DLL签名验证
│   ├── MWController.cs              # 窗口控制器 (实现 IController)
│   ├── Setting.cs                   # 游戏设置 (LPS 持久化)
│   ├── Win32.cs                     # Win32 API P/Invoke
│   └── Reply.mbconfig               # 聊天自动回复配置
│
├── WinDesign/                       # 游戏窗口
│   ├── winCharacterPanel.xaml/.cs   # 角色属性面板
│   ├── winGameSetting.xaml/.cs      # 游戏设置窗口
│   ├── winBetterBuy.xaml/.cs        # 批量购买窗口
│   ├── winConsole.xaml/.cs          # 开发者控制台
│   ├── winReport.xaml/.cs           # 反馈报告窗口
│   ├── winInputBox.xaml/.cs         # 文本输入框
│   ├── winInventory.xaml/.cs        # 物品库存
│   ├── winGallery.xaml/.cs          # 图鉴查看器
│   ├── winWorkMenu.xaml/.cs         # 工作菜单
│   ├── winMoveArea.xaml/.cs         # 移动区域设置
│   ├── DIYViewer.xaml/.cs           # 自定义 DIY 查看器
│   └── TalkSelect.xaml/.cs          # 对话选项
│
├── MutiPlayer/                      # 多人联机
│   ├── MPController.cs / MPMOD.cs
│   ├── MPUserControl.xaml/.cs
│   ├── MPFriends.xaml/.cs
│   └── winMutiPlayer.xaml/.cs
│
├── Design/                          # UI 辅助
│   ├── AutoUniformGrid.cs
│   └── Converters/
│
├── Res/                             # 资源文件 (字体、图像)
├── mod/0000_core/                   # 核心 MOD (默认宠物、食物、翻译)
│   ├── pet/vup/                     # 默认猫娘动画帧 (数千张PNG)
│   ├── food/                        # 食物 LPS 定义
│   ├── image/                       # 食物/物品/工作图片
│   ├── lang/                        # 多语言文本 (en/, zh-Hans/, zh-Hant/)
│   ├── file/                        # ZIP LPS 包 (表情、插画等)
│   └── info.lps                     # 核心 MOD 清单
│
└── Properties/                      # 程序集属性
```

### 6.2 启动流程

```
App.xaml.cs
  │
  ├── 1. 查找所有 Setting*.lps 文件 (多存档支持)
  ├── 2. 创建 MainWindow 实例
  └── 3. 全局异常处理 (含 MOD 错误归属)

MainWindow 构造函数:
  │
  ├── 1. 解析命令行参数 (prefix, linux)
  ├── 2. 设置 PNG 动画内存限制
  ├── 3. 初始化 Steam (如有)
  ├── 4. 迁移旧存档格式
  └── 5. GameInitialization():
        ├── 加载 Setting.lps
        ├── 设置窗口样式 (透明、Alt+Tab隐藏)
        ├── 加载 MOD (mod\ 目录 + Steam 创意工坊)
        └── GameLoad(path):
              ├── 扫描每个 MOD 目录 → CoreMOD 解析
              ├── 加载 主题、宠物、食物、图片、文件、图鉴、文本、语言、插件
              ├── 通过 PetLoader 创建 GraphCore
              ├── 创建 Main (核心显示控制器)
              ├── 加载存档 (含备份/版本迁移)
              ├── 注册钩子 (音乐、Steam、诊断、自动保存)
              ├── 初始化 UI (工具栏、菜单、设置、购买、对话、托盘)
              └── 调用所有插件的 LoadPlugin()

关闭流程:
  └── 播放 Shutdown 动画 → Save() → EndGame() → 清理
```

### 6.3 CoreMOD — MOD 加载器

`CoreMOD.cs` 是 MOD 系统的核心，处理以下职责：

```
mod/<name>/info.lps   ← MOD 元数据清单
  │
  ├── theme/    → 加载主题 (颜色 + 图像)
  ├── pet/      → 加载宠物模型 (动画 + 工作定义)
  ├── food/     → 加载食物定义
  ├── image/    → 加载共享图片
  ├── file/     → 加载共享文件 (ZIP LPS 包等)
  ├── photo/    → 加载图鉴照片 (含解锁条件)
  ├── text/     → 加载 ClickText, SelectText, LowText
  ├── lang/     → 加载翻译
  └── plugin/   → 加载 .NET DLL → 数字签名验证 → 创建 MainPlugin 实例
```

**代码签名信任：** LB Game 或 DigiCert/Asseco 签名的 DLL 自动信任，其他需 `PassMOD` 设置开启。

### 6.4 音乐检测

通过 NAudio 每 200ms 采样系统音频输出。音量超过 `MusicCatch` 时宠物开始跳舞，超过 `MusicMax` 时播放更激烈的变体。

---

## 7. VPet-Simulator.Tool — MOD 制作工具

一个简单的 .NET Framework 4.8 控制台工具 (`Program.cs`)，当前实现：

**功能：动画去重与重命名**

- 扫描文件夹内的 PNG 帧
- 通过 SHA 哈希比较相邻帧，删除重复帧并延长前一帧持续时间
- 重命名为 `{name}_{frameId:000}_{duration}ms.png` 格式
- 这是宠物加载器自动检测系统期望的标准命名格式

---

## 8. VPet.Solution — 设置编辑器与存档查看器

独立的 WPF 应用 ("VPET 问题解决工具")，采用 MVVM 架构：

```
Views/           # 页面/窗口
  SettingEditor/     # 6个设置标签页 (系统/自定义/交互/图形/诊断/MOD)
  SaveViewer/        # 存档数据和统计查看
ViewModels/      # 对应的 ViewModel
Models/          # 数据模型
Converters/      # 17 个 XAML 值转换器
SimpleObservable/ # 可观察模式基础设施 (INotifyPropertyChanged 扩展)
Utils/           # 工具类
```

---

## 9. 动画系统详解

### 9.1 GraphInfo — 动画三维分类

每个动画都有三个关键维度：

| 维度 | 类型 | 取值 |
|------|------|------|
| **GraphType** | 动画种类 | `Default` (呼吸), `Move`, `Sleep`, `Say`, `Touch_Head`, `Touch_Body`, `Raised_Dynamic`, `Raised_Static`, `Idel`, `Work`, `StartUP`, `Shutdown`, `StateONE`, `StateTWO`, `SideHide_*`, `Switch_*` 等 25+ 种 |
| **AnimatType** | 动画段式 | `A_Start` (入场), `B_Loop` (循环), `C_End` (退场), `Single` (独立) |
| **ModeType** | 宠物状态 | `Happy`(0), `Nomal`(1), `PoorCondition`(2), `Ill`(3) |

### 9.2 动画播放流程 (ABC 系统)

```
A_Start (入场) → B_Loop (循环N次或随机时长) → C_End (退场) → 下一个动画
```

- 基础动画 (呼吸) 使用 `Single` 模式直接循环
- 交互动画 (摸头/摸身体) 经历完整的 A→B→C 流程
- B_Loop 的循环次数可以随机，实现自然的动画变化

### 9.3 三种动画实现

| 类 | 输入源 | 渲染机制 |
|----|--------|----------|
| **PNGAnimation** | 文件夹内的 PNG 帧序列 | 通过 SkiaSharp 合成精灵图，运行时用 CroppedBitmap 裁剪。预缓存 2 帧。 |
| **Picture** | 单个 PNG 文件 | 显示一帧，持续 `Length` 时长。支持循环。 |
| **FoodAnimation** | LPS 配置 + 引用的动画 | 三层复合 (后层+食物图+前层)，每层独立变换/透明度。用于进食动画。 |

### 9.4 动画查找 — 状态回退逻辑

`GraphCore.FindGraph(Name, AnimatType, Mode)` 的状态匹配策略：

```
1. 尝试精确匹配 ModeType
2. 尝试更高状态 (更开心)
3. 尝试更低状态 (更不开心)
4. 尝试任意非生病状态
5. 返回找到的第一个 (可能为 null)
```

这确保了即使某种状态缺少动画，宠物也能展示合理的后备动画。

### 9.5 路径命名约定

```
{startup_path}_{状态}_{动画类型}_{动画名称}_{动作}_{帧延迟}ms.png
```

- `/` 和 `_` 可以互换
- 顺序不重要 (解析器自动识别关键词)
- 示例：`happy/touch_head/pet_a_125.png` → 开心状态, 摸头动画, "pet"名称, A段, 125ms帧间隔
- 解析优先级：配置文件 > 路径关键词 > 默认值

### 9.6 动画类型详解

**7 种必需动画：**
`Raised_Dynamic` `Raised_Static` `Default` `Sleep` `Say` `StartUP` `Work`

**8 种支持 ABC 三段式的动画：**
`Raised_Static` `Touch_Head` `Touch_Body` `Idel` `Sleep` `Say` `StateONE` `StateTWO` `Work`

**完整动画类型表：**

| GraphType | 含义 | 必需 | ABC 支持 |
|-----------|------|:---:|:--------:|
| `Default` | 呼吸/空闲 | ✓ | 仅 Single |
| `Raised_Dynamic` | 被拖拽提起 (动态) | ✓ | 仅 Single |
| `Raised_Static` | 被提起 (静态) | ✓ | ✓ |
| `Sleep` | 睡觉 | ✓ | ✓ |
| `Say` | 说话 | ✓ | ✓ |
| `StartUP` | 启动动画 | ✓ | 仅 Single |
| `Work` | 工作 | ✓ | ✓ |
| `Touch_Head` | 摸头反应 | | ✓ |
| `Touch_Body` | 摸身体反应 | | ✓ |
| `Move` | 移动 | | ✓ |
| `Idel` | 空闲 (蹲下、无聊等) | | 两种 |
| `StateONE` | 状态 1 | | ✓ |
| `StateTWO` | 状态 2 | | ✓ |
| `Shutdown` | 关机动画 | | 仅 Single |
| `Switch_Up/Down` | 状态切换过渡 | | 仅 Single |
| `Switch_Thirsty/Hunger` | 饥渴状态切换 | | 仅 Single |
| `Common` | 通用/自定义 | | — |

---

## 10. MOD 系统详解

### 10.1 MOD 目录结构

```
mod/
├── 0000_core/             # 核心 MOD (内置，必须存在)
│   ├── info.lps           # 清单: name, author, ver, gamever, intro, tag
│   ├── icon.png           # MOD 图标
│   ├── pet/vup/           # 宠物动画 (海量子目录)
│   ├── food/              # 食物定义 (.lps)
│   ├── image/             # 图片资源
│   ├── lang/              # 多语言文本
│   ├── file/              # 数据包 (.zlps)
│   └── plugin/            # 如果有代码插件
│
├── 0001_some_mod/         # 第三方 MOD
│   └── ... (同上结构)
│
└── Steam Workshop Items/  # 创意工坊 MOD (自动下载)
    └── ...
```

### 10.2 info.lps 格式示例

```
mod#0000_core:|name:核心模组|author:LB Game|ver:1.0.0|gamever:11068|intro:VPet核心模组|tag:core,default
```

### 10.3 MOD 子目录功能

| 子目录 | 功能 | 加载方式 |
|--------|------|----------|
| `theme/` | 主题 (颜色 + 图片) | 覆盖 `Theme.xaml` 中的资源 |
| `pet/` | 宠物模型 (动画 + 工作) | `PetLoader` 扫描 → `GraphCore` |
| `food/` | 食物定义 | 解析 LPS → `Food` 对象 |
| `image/` | 共享图片 | `ImageResources` 缓存 |
| `file/` | 共享文件 | `FileSources` 列表 |
| `photo/` | 图鉴照片 | `Photo` 对象 (含条件解锁) |
| `text/` | 对话文本 | ClickText, SelectText, LowText |
| `lang/` | 翻译 | 覆盖默认语言文本 |
| `plugin/` | 代码插件 | 加载 DLL → 签名验证 → 实例化 |

### 10.4 插件加载流程

```
CoreMOD 扫描 mod/<name>/plugin/
  │
  ├── 找到 .dll 文件
  ├── 检查数字签名
  │     ├── LB Game / DigiCert / Asseco → 信任
  │     ├── 其他 → 检查 Setting["PassMOD"]
  │     └── 未通过 → 拒绝加载
  ├── 反射查找 MainPlugin 子类
  ├── 实例化: new MyPlugin(IMainWindow)
  ├── 加入 Plugins 字典
  └── 稍后调用 LoadPlugin() (在游戏数据就绪后)
```

---

## 11. 数据格式 — LinePutScript (LPS)

VPet 全项目使用 **LinePutScript (LPS)** 格式进行配置、存档和 MOD 元数据的序列化。这是一种面向行的自定义格式。

### 基本语法

```
line_name#info:|sub1:value1|sub2:value2|&sub3:value3
```

| 符号 | 说明 |
|------|------|
| `#` | 分隔名称和 info |
| `:|` | 子分隔符 |
| `|` | 子项分隔符 |
| `&` | 数组型子项前缀 |

### 实际示例

```
# 食物定义
food#拉面:|StrengthFood:80|StrengthDrink:10|Feeling:8|Exp:15|Price:20|Type:Meal

# 存档数据
money:|100.5
exp:|42
level:|3
```

### 代码中的使用

```csharp
// 反序列化
var food = LPSConvert.DeserializeObject<Food>(line);

// 序列化
var line = LPSConvert.SerializeObject(food, "food");
```

---

## 12. 功能添加指南

### 12.1 添加新食物

**最简单的 MOD 方式 — 无需写代码：**

**步骤 1** — 在 `mod/<你的mod>/food/` 下创建 `.lps` 文件：

```
food#巧克力蛋糕:|StrengthFood:60|StrengthDrink:5|Feeling:15|Exp:10|Price:25|Type:Snack|Graph:eat_cake
```

**步骤 2** — 将食物图片 `food_巧克力蛋糕.png` 放到 `mod/<你的mod>/image/` 目录下。

**步骤 3** — 如果想让宠物在进食时播放特定动画，在宠物动画目录下添加对应的进食动画帧。

**如果需要添加新的 FoodType 类别 (如 "保健品")：**
1. 在 `Mod/Food.cs` 的 `FoodType` 枚举中添加新值
2. 在 `winBetterBuy.xaml.cs` 中添加对应的 UI 展示逻辑
3. 在翻译文件中添加对应文本

### 12.2 添加新动画

**方式 A — 自动识别 (无需配置)**

1. 在宠物模型的动画路径下新建文件夹，例如 `mod/*/pet/vup/new_anim/`
2. 按照命名规范放置 PNG 帧文件：
   ```
   new_anim_000_125.png   # 第0帧, 125ms
   new_anim_001_125.png   # 第1帧, 125ms
   new_anim_002_150.png   # 第2帧, 150ms
   ```
3. 文件夹名和文件名中的关键词会被自动解析为 GraphType 和 AnimatType
   - 包含 `sleep` → Sleep 类型
   - 包含 `touch_head` → Touch_Head 类型
   - 包含 `a_` → A_Start 段式
   - 包含 `happy` → Happy 状态

**方式 B — 手动 info.lps 配置**

在动画文件夹内创建 `info.lps`：

```
# 普通 PNG 动画
graph#new_anim:|mode:happy|animat:a|loop:0|type:Touch_Head

# 多层食物动画 (FoodAnimation)
graph#eat_cake:|mode:nomal|animat:a|loop:0|type:Eat|front_lay:eat_front|back_lay:eat_back
a0:|x:0|y:0|op:1|time:100
a1:|x:5|y:-2|op:0.9|time:150
```

**方式 C — 使用 VPet-Simulator.Tool 预处理**

1. 将所有帧放到一个文件夹
2. 运行 `VPet-Simulator.Tool.exe`
3. 选择 "动画去重与重命名" 功能
4. 工具会自动删除重复帧并生成标准命名格式的文件

### 12.3 添加新行为/互动

**在代码插件中添加：**

```csharp
public override void LoadPlugin()
{
    // 方式1: 添加到定时循环 (每15秒执行)
    MW.Main.TimeHandle += (main) =>
    {
        // 你的定时逻辑
        // 例如：检查时间、修改属性、触发特殊事件
    };

    // 方式2: 添加到随机交互列表 (宠物空闲时随机触发)
    MW.Main.RandomInteractionAction.Add(() =>
    {
        // 例如：10% 概率触发特殊动作
        if (new Random().Next(100) < 10)
        {
            MW.Main.SayRnd("今天天气真好！");
            return true;  // true = 本次已触发, 不再尝试其他随机行为
        }
        return false;     // false = 未触发, 继续尝试其他随机行为
    });

    // 方式3: 添加资源消耗处理
    MW.Main.FunctionSpendHandle += (main, timePass) =>
    {
        // 自定义资源消耗/恢复逻辑
    };
}
```

### 12.4 创建代码插件 (Plugin)

这是最强大的功能扩展方式。

**步骤 1** — 创建 .NET 8.0 类库项目：

```xml
<!-- MyVPetPlugin.csproj -->
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0-windows</TargetFramework>
    <UseWPF>true</UseWPF>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="VPet-Simulator.Windows.Interface">
      <HintPath>..\VPet\VPet-Simulator.Windows.Interface\bin\Release\net8.0-windows\VPet-Simulator.Windows.Interface.dll</HintPath>
    </Reference>
  </ItemGroup>
</Project>
```

**步骤 2** — 编写插件主类：

```csharp
using VPet_Simulator.Windows.Interface;

namespace MyVPetPlugin
{
    public class MyPlugin : MainPlugin
    {
        // 必须与 MOD 目录名完全一致
        public override string PluginName => "MyPlugin";

        public MyPlugin(IMainWindow mw) : base(mw) { }

        public override void LoadPlugin()
        {
            // 游戏数据已就绪，可以访问 MW.Core, MW.Main, MW.Set 等
            // 在这里注册事件、添加工具栏按钮、创建自定义 UI 等

            // 示例：添加工具栏按钮
            // MW.LoadDIY(); // 在 LoadDIY() 中创建工具栏项
        }

        public override void GameLoaded()
        {
            // 所有 MOD 均已加载完毕
        }

        public override void Save()
        {
            // 将自定义数据写入存档
            // MW.GameSavesData["MyPluginData"] = myData;
        }

        public override void Setting()
        {
            // 打开插件设置窗口 (可选)
            // new MySettingsWindow(MW).ShowDialog();
        }

        public override void LoadDIY()
        {
            // 添加自定义工具栏按钮 (可选)
        }

        public override void EndGame()
        {
            // 清理资源
        }
    }
}
```

**步骤 3** — 打包部署：

1. 编译 DLL (含所有依赖)
2. 创建目录 `mod/MyPlugin/plugin/`
3. 将 DLL 和依赖放入该目录
4. 创建 `mod/MyPlugin/info.lps`：
   ```
   mod#MyPlugin:|name:我的插件|author:你的名字|ver:1.0.0|gamever:11068|intro:插件描述|tag:plugin,tool
   ```
5. 可放入 `mod/MyPlugin/icon.png` 作为图标

**步骤 4 (可选)** — 代码签名。如果你的 DLL 不是由 LB Game 或 DigiCert/Asseco 签名，用户需要在设置中开启 `PassMOD` 才能加载你的插件。

### 12.5 添加新物品类型

```csharp
public override void LoadPlugin()
{
    // 1. 注册物品工厂
    Item.Creators["Wearable"] = (imw, line) =>
    {
        var wearable = new Wearable(line);
        wearable.IMW = imw;
        return wearable;
    };

    // 2. 注册使用动作
    Item.UseAction["Wearable"] = new List<Func<IMainWindow, Item, bool>>
    {
        (imw, item) =>
        {
            var wearable = item as Wearable;
            if (wearable == null) return false;
            // 执行装备逻辑
            imw.Main.SayRnd($"穿上了 {wearable.Name}！");
            // 将装备保存到 DynamicResources
            imw.DynamicResources["CurrentWearable"] = wearable;
            return true;
        }
    };
}

// 自定义物品子类
public class Wearable : Item
{
    public string Slot { get; set; }
    public int DefenseBonus { get; set; }

    public Wearable(Line line) : base(line)
    {
        Slot = line["Slot"];
        DefenseBonus = line["DefenseBonus"].ToInt();
    }
}
```

### 12.6 添加新窗口/UI

**在插件中创建 WPF 窗口：**

```csharp
public override void LoadPlugin()
{
    // 注册到主窗口的 Windows 集合 (方便统一管理)
    // 或者通过工具栏按钮触发
}

// 自定义窗口
public class MyWindow : WindowX  // 使用 Panuon.WPF.UI 的 WindowX
{
    public MyWindow(IMainWindow mw)
    {
        // 使用 Panuon 主题保持视觉一致性
        // 设计你的 UI
    }
}
```

### 12.7 添加新主题

在 `mod/<你的mod>/theme/` 目录下创建主题文件：

1. 创建颜色和样式定义文件
2. 引用 `VPet-Simulator.Core/Display/Theme.xaml` 中的资源命名
3. 在 `info.lps` 中声明主题

主题系统基于 WPF ResourceDictionary 覆盖机制，可以重新定义颜色、字体、图片等所有视觉资源。

### 12.8 添加新语言/翻译

在 `mod/<你的mod>/lang/<语言代码>/` 目录下创建翻译文件：

```
mod/<你的mod>/lang/ja/       # 日文
  ├── Base.lps               # 基础 UI 文本
  ├── food.lps               # 食物名称翻译
  ├── textclick.lps          # 点击对话翻译
  ├── textselect.lps         # 对话选项翻译
  └── ...
```

翻译文件使用 LPS 格式，key 与源语言一致，value 为翻译文本。

### 12.9 添加新宠物模型

在 `mod/<你的mod>/pet/<宠物名>/` 目录下创建完整动画集：

```
pet/my_pet/
├── info.lps                   # 宠物定义 (名称、缩放、工作列表等)
├── Default/                   # 呼吸动画
│   └── default_000_100.png ...
├── Sleep/
│   ├── nomal/
│   │   ├── sleep_A_000_125.png ...
│   │   ├── sleep_B_000_125.png ...
│   │   └── sleep_C_000_125.png ...
│   └── happy/ ...
├── Touch_Head/
│   ├── nomal/a/ ...
│   ├── nomal/b/ ...
│   └── nomal/c/ ...
├── Touch_Body/ ...
├── Move/ ...
├── Say/ ...
├── StartUP/
│   └── startup_000_100.png ...
├── Work/ ...
├── Raised_Dynamic/ ...
├── Raised_Static/ ...
└── Shutdown/ ...
```

动画帧命名遵循 [动画系统详解](#9-动画系统详解) 中的约定即可被自动识别。

### 12.10 添加新工作类型

在宠物模型的 `info.lps` 中定义 Work：

```
work#编程:|MoneyBase:25|StrengthFood:30|StrengthDrink:10|Feeling:-5|Time:60|FinishBonus:50|LevelLimit:1
```

或者，在代码插件中动态创建 Work：

```csharp
public override void LoadPlugin()
{
    var customWork = new Work()
    {
        Name = "写小说",
        MoneyBase = 20,
        StrengthFood = 25,
        StrengthDrink = 5,
        Feeling = -8,
        Time = 120,
        FinishBonus = 100,
        LevelLimit = 2
    };
    // 添加到 GraphHelper...
}
```

---

## 13. 关键文件速查表

| 角色 | 文件路径 |
|------|----------|
| **应用入口** | `VPet-Simulator.Windows/App.xaml.cs` |
| **主窗口生命周期** | `VPet-Simulator.Windows/MainWindow.xaml.cs` |
| **主窗口 IMainWindow 实现** | `VPet-Simulator.Windows/MainWindow.cs` |
| **主窗口属性** | `VPet-Simulator.Windows/MainWindow_Property.cs` |
| **核心显示控件** | `VPet-Simulator.Core/Display/Main.xaml` + `.xaml.cs` |
| **动画显示管理** | `VPet-Simulator.Core/Display/MainDisplay.cs` |
| **模拟循环 (核心逻辑)** | `VPet-Simulator.Core/Display/MainLogic.cs` |
| **动画注册表** | `VPet-Simulator.Core/Graph/GraphCore.cs` |
| **动画接口** | `VPet-Simulator.Core/Graph/IGraph.cs` |
| **动画元数据** | `VPet-Simulator.Core/Graph/GraphInfo.cs` |
| **工作/移动定义** | `VPet-Simulator.Core/Graph/GraphHelper.cs` |
| **PNG 动画渲染器** | `VPet-Simulator.Core/Graph/PNGAnimation.cs` |
| **进食多层动画** | `VPet-Simulator.Core/Graph/FoodAnimation.cs` |
| **静态图渲染** | `VPet-Simulator.Core/Graph/Picture.cs` |
| **根游戏数据容器** | `VPet-Simulator.Core/Handle/GameCore.cs` |
| **存档接口** | `VPet-Simulator.Core/Handle/IGameSave.cs` |
| **完整存档实现** | `VPet-Simulator.Windows.Interface/GameSave_VPet.cs` |
| **存档 v2 管理器** | `VPet-Simulator.Windows.Interface/GameSave_v2.cs` |
| **宠物加载器** | `VPet-Simulator.Core/Handle/PetLoader.cs` |
| **食物接口** | `VPet-Simulator.Core/Handle/IFood.cs` |
| **窗口控制器接口** | `VPet-Simulator.Core/Handle/IController.cs` |
| **窗口控制器实现** | `VPet-Simulator.Windows/Function/MWController.cs` |
| **MOD 加载器** | `VPet-Simulator.Windows/Function/CoreMOD.cs` |
| **设置引擎** | `VPet-Simulator.Windows/Function/Setting.cs` |
| **插件基类** | `VPet-Simulator.Windows.Interface/MainPlugin.cs` |
| **插件 API 接口** | `VPet-Simulator.Windows.Interface/IMainWindow.cs` |
| **设置接口** | `VPet-Simulator.Windows.Interface/ISetting.cs` |
| **食物类** | `VPet-Simulator.Windows.Interface/Mod/Food.cs` |
| **物品基类** | `VPet-Simulator.Windows.Interface/Mod/Item.cs` |
| **点击对话文本** | `VPet-Simulator.Windows.Interface/Mod/ClickText.cs` |
| **条件文本** | `VPet-Simulator.Windows.Interface/Mod/ICheckText.cs` |
| **对话选项** | `VPet-Simulator.Windows.Interface/Mod/SelectText.cs` |
| **图鉴系统** | `VPet-Simulator.Windows.Interface/Mod/Photo.cs` |
| **工作排程** | `VPet-Simulator.Windows.Interface/ScheduleTask.cs` |
| **扩展方法** | `VPet-Simulator.Windows.Interface/ExtensionFunction.cs` |
| **对话数据** | `VPet-Simulator.Core/Handle/SayInfo.cs` |
| **工作计时器 UI** | `VPet-Simulator.Core/Display/WorkTimer.xaml` |
| **对话气泡** | `VPet-Simulator.Core/Display/MessageBar.xaml` |
| **工具栏** | `VPet-Simulator.Core/Display/ToolBar.xaml` |
| **动画制作工具** | `VPet-Simulator.Tool/Program.cs` |
| **设置编辑器入口** | `VPet.Solution/App.xaml.cs` |

---

## 14. 架构决策与设计模式

| 模式 | 应用位置 | 说明 |
|------|----------|------|
| **接口隔离** | `IController`, `IGameSave`, `IFood`, `IMainWindow` | 核心与具体实现解耦 |
| **工厂模式** | `Item.Creators`, `IGraphConvert` | 可扩展的类型创建 |
| **策略模式** | `Item.UseAction` 字典 | 可注册的自定义物品使用逻辑 |
| **观察者模式** | `TimeHandle`, `FunctionSpendHandle`, `RandomInteractionAction` | 插件可订阅的事件钩子 |
| **双缓冲** | `MainDisplay` (PetGrid + PetGrid2) | 无闪烁动画切换 |
| **MVVM** | `VPet.Solution` | 设置编辑器的架构 |
| **三段式状态机** | A_Start → B_Loop → C_End | 动画播放生命周期 |

### 核心设计原则

1. **MOD 优先 — 大多数新功能应通过 MOD 实现而非修改核心代码。** 这是项目作者明确的设计意图。参考 [VPet.Plugin.Demo](https://github.com/LorisYounger/VPet.Plugin.Demo) 了解官方插件示例。

2. **LPS 统一数据格式 — 从配置到存档到 MOD 定义全部使用 LPS 格式**，这是一种比 JSON/XML 更节省空间的面向行格式，适合手工编辑。

3. **签名信任链 — 代码插件需要通过数字签名验证**，防止恶意 MOD。开发时需注意此限制。

4. **Core 可嵌入 — `VPet-Simulator.Core` 设计为可独立嵌入任何 WPF 应用**，通过 `IController` 和 `IGameSave` 接口适配不同宿主。

---

## 15. 构建与调试

### 15.1 首次构建

1. 使用 Visual Studio 2022 打开 `VPet.sln`
2. 选择 **x64** 平台，启动项目设为 **VPet-Simulator.Windows**
3. 首次运行会报错"缺少Core模组，无法启动桌宠"——这是正常的
4. **以管理员身份** 运行 `mklink.bat`，将 `mod` 文件夹符号链接到构建输出目录
5. 重新运行即可

### 15.2 调试技巧

- **开发者控制台**：在主窗口设置中开启 `DeBug` 模式，右键宠物可以打开 `winConsole`
- **MOD 错误归属**：异常处理会自动标注是哪个 MOD 导致的错误
- **存档位置**：存档以 `Setting*.lps` 文件存储在应用目录下

### 15.3 贡献代码

如果要向官方仓库提交代码：

1. **Bug 修复**：直接提交 PR 即可
2. **新功能/玩法**：必须先联系作者 (zoujin.dev@exlb.org 或 GitHub Issue)，确保功能适合项目
3. 作者可能修改、删减提交的代码以确保适配
4. 记住：**大多数新功能可以通过插件 MOD 实现，无需修改核心源代码**

---

> 📖 **参考资源**
> - 官方仓库: https://github.com/LorisYounger/VPet
> - 插件示例: https://github.com/LorisYounger/VPet.Plugin.Demo
> - MOD 制作工具: https://github.com/LorisYounger/VPet.ModMaker
> - NuGet 包: VPet-Simulator.Core
> - Steam 页面: https://store.steampowered.com/app/1920960/VPet/

---

*文档生成日期: 2026-07-09 | 基于 VPet-Simulator 代码分析*
