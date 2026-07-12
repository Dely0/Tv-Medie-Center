---
name: write-windows-script
description: >
  TRIGGER when user asks to create/edit/modify/generate .bat .ps1 .vbs PowerShell script.
  Or when outputting batch/PowerShell code blocks. Handles Chinese Windows GBK/UTF-8
  encoding, correct escaping for > | & ^ in echo, and proper PowerShell nesting.
  ALWAYS check encoding before writing any Windows script file.
---

# Windows Script Writing (bat / ps1 / vbs)

## 为什么必须用这个 Skill

**问题根因**：中文 Windows 的 cmd.exe 默认编码是 GBK(CP936)，而你生成的文件是 UTF-8。
如果 bat 文件中有中文字符或特殊符号 `> | & ^`，不处理编码就直接用 Write 写入 .bat 文件，
执行时必然乱码或解析失败。

## 核心规则

### 1. bat 文件头
每个 .bat 文件正文第一行必须加：
```bat
@echo off
chcp 65001 >nul
```

### 2. 文件编码
Write 工具写入时默认是 UTF-8（不含 BOM），这是正确的。**不要额外添加 BOM**。

### 3. echo 特殊字符转义

| 字符 | 在 echo 中写成 | 原因 |
|------|---------------|------|
| `>` | `^>` | 否则被 shell 解释为重定向 |
| `<` | `^<` | 否则被 shell 解释为输入 |
| `\|` | `^\|` | 否则被 shell 解释为管道 |
| `&` | `^&` | 否则被 shell 解释为命令连接 |
| `^` | `^^` | `^` 本身就是转义符，要输出字面量需要双写 |

```bat
@echo off
chcp 65001 >nul
echo 输出文件描述符: ^>
echo 百分号记得双写: %%
echo at符号不需要转义: @
```

### 4. 生成 bat 的 bat（双层转义）

如果一个 bat 脚本通过 `echo` 来**生成另一个 bat 文件**（如 install-startup.bat），
每层 echo 都会消耗一层转义：

**外层 bat 的 echo** → **写入的目标 bat** → **目标 bat 执行时的效果**

```
外层 echo 写: ^>   → 目标文件得到: >   → 执行时: 重定向
外层 echo 写: ^^>  → 目标文件得到: ^>  → 执行时: 输出 >
外层 echo 写: ^^>nul → 目标文件得到: ^>nul → 执行时: 输出 >nul
外层 echo 写: ^>nul  → 目标文件得到: >nul  → 执行时: 重定向到 nul
```

示例模板（外层 bat 生成内层 bat）：
```bat
(
echo @echo off
echo chcp 65001 ^^>nul
echo start "" /B "%PY%" -X utf8 main.py ^> data\server.log 2^>^&1
echo start msedge.exe --start-fullscreen --new-window http://localhost:8080
) > "%TARGET%"
```

注意 `>nul` 不用转义（它是 echo 命令之外的重定向），但 `^^>nul` 中：
- 外层 echo `^^` → 写入文件得到 `^`
- `>nul` 是外层 bat 的重定向 → 但它已经在 `echo` 内部，所以被写入
- 实际上外层 echo `^^>nul` → 目标文件写入 `^>nul`
- 内层执行时 `^>nul` → `>` 被转义，输出 `>nul`

**口诀**：外层 echo 中特殊字符前加 `^` 或 `^^`，取决于你想让目标 bat 得到 `>` 还是 `^>`。

### 5. PowerShell 嵌入

在 bat 中嵌入 PowerShell 命令时：
- **用单行字符串**，不要用 `^` 续行跨多行
- **PowerShell 内部字符串用单引号 `'...'`**，避免与 bat 的 `"` 冲突

✅ 正确：
```bat
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell;$r=$w.AppActivate('TV Media Center');if($r){$w.SendKeys('{F11}')}"
```

❌ 错误：
```bat
powershell -NoProfile -Command ^
  $w=New-Object -ComObject WScript.Shell; ^
  ...
```

### 6. Python 命令
```bat
python -X utf8 main.py
```
`-X utf8` 确保 Python 的 I/O 用 UTF-8 而非 GBK。

### 7. Write 前检查清单
- [ ] 文件头有 `chcp 65001 >nul`？
- [ ] echo 中的 `>` `|` `&` `^` 是否正确转义？
- [ ] 如果是生成 bat 的 bat，转义是否正确加倍？
- [ ] PowerShell 命令是否是单行？
- [ ] PowerShell 内部引号用的是单引号 `'` 而非双引号 `"`？
- [ ] Python 调用加了 `-X utf8`？

## 反面案例（本项目历史教训）

1. `restart.bat` 中 PowerShell `SendKeys` 初期使用了多行 `^` 续行 → 行尾空格导致语法错误。修复为单行
2. `install-startup.bat` 中 `echo` 生成 `>nul` 时转义错误 → 自启动脚本无法正常工作。修复为正确的双层转义
3. Python 测试脚本不带 `-X utf8` → `urllib.parse.urlencode` 在 ASCII 模式下抛 UnicodeEncodeError
