# SDU OS PPTs

> ZHS这个平台过于恶心人导致的
>
> 感谢 [fuckZHS](https://github.com/VermiIIi0n/fuckZHS)

因为ZHS自己会检测,所以直接暴力通过proxifier转发整个模拟器的流量再抓包即可得到需要的内容

## How

先读取环境变量或 `.env` 中的 `AUTHORIZATION`

若未提供 `AUTHORIZATION`或 `.env` 中的 `AUTHORIZATION` 已过期,会自动保存用于登录的二维码

- 用 App 扫码确认
- 自动提取有效 `AUTHORIZATION` 并写回 `.env`,随后删除 `qr_login.png`

## Output

- 下载文件在 `text/`
- 生成 `download_summary.md`

## Usage

```nushell
uv run main.py
```
