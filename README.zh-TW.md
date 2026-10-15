# landline-agent

[English](README.md) | **繁體中文**

**讓 AI 用你家的市話幫你打電話；要不要撥，由你按。**

https://github.com/user-attachments/assets/831f0381-d4d5-4351-94d7-0005849f6a20

> 📞 **只需要電話系統、不需要 AI？** 單獨用 [landline-hub](https://github.com/luna61ouo/landline-hub) 就好：市話來電響到手機、總機把分機轉給同事、用手機透過市話撥出。

訂位、問營業時間、問店裡有沒有某樣東西。你的 AI（或任何程式）交出一份簡短的簡報，語音模型用自然的台灣華語打這通電話，結束後把逐字稿和錄音交回給你。

它建在 [landline-hub](https://github.com/luna61ouo/landline-hub) 上，電話線歸 hub 管。語音模型是 Gemini Live。

## 運作方式

```
 你的 AI／程式 ──申請──▶ landline-agent ──（核准了嗎？）──▶ landline-hub ──▶ 市話 ──▶ 店家
                              │      ▲
                         核准單      │ 你按「同意」
                              ▼      │
                     核准介面（Discord 按鈕，或你自己做的）
```

1. 你的 AI 把 `{number, brief, label, purpose}`（號碼、簡報、打給誰、為什麼）送到**申請端 API**。
2. 號碼在白名單上就直接撥。不在的話，會開一張**核准單**等人決定：打給誰、為什麼，以及語音模型會照著講的完整簡報。
3. 你在核准介面按同意或拒絕。按了同意，agent 才請 hub 撥號，而且只撥這一通。
4. 語音模型跟對方通話。它會等真人接起來才開口（類比市話不會說對方接了沒，是 hub 在聽），背景再吵也講得下去，事情辦完就掛斷。
5. 結果（怎麼結束的、逐字稿、錄音）從同一個 API 拿回來。

## 安全設計

提出申請的 AI，和真正撥號的程式，是兩個不同的程式、拿兩把不同的鑰匙。

- **AI 只能提出申請。** 它手上只有申請鑰匙。撥號在 agent 裡面進行，而且一定要有撥號許可；許可只有核准流程（或你的白名單）能發。
- **AI 不能核准自己。** 核准走另一個入口（權限 600 的 unix socket），用另一把鑰匙。核准介面是另一個獨立程式。白名單關掉時，這個入口根本不存在。
- **你核准的內容，就是會講出去的內容。** `bind_brief`（預設開）會在核准後鎖住簡報；要改簡報，舊的核准單會作廢、重新問你一次。
- **電話裡的語音模型沒有任何工具**，查不了資料、也不能做任何動作，只能講簡報裡有的內容。對方說的話回到你這裡只是資料，不會變成指令。
- **硬規則放最後。** 給模型的指示依序是：簡報 → 任務準則 → 語氣 → 硬規則，所以簡報裡寫什麼都蓋不過硬規則。
- **撥不出緊急電話。** agent 只接受一般電話號碼（台灣：0 開頭再接 7 到 9 位數字），110、119 等短碼在一開始就被擋下。hub 那邊會再擋一次：agent 這個角色本來就不能撥緊急或付費號碼。
- **每通電話都有黑盒子**：模型實際收到的指示、時間軸、原始錄音和逐字稿。

> ⚠️ **白名單可以關掉**（`[allowlist] enabled = false`）。關掉的同時核准也一起關掉：每一通申請都會自動撥出，中間沒有人確認。要不要這樣由你決定，我們建議保持開啟。

### 「AI 不能核准自己」有多牢靠？

看你怎麼部署。請誠實判斷自己是哪一種：

| 部署方式 | AI 和核准之間隔著什麼 |
|---|---|
| **AI 用一個系統帳號跑**，agent 用另一個 | 檔案權限。AI 讀不到核准鑰匙、agent 連 hub 的憑證、白名單和設定檔，也改不了 agent 的程式。這是我們建議的做法。 |
| **AI 和 agent 用同一個系統帳號**（個人電腦常見） | 只有你另外替 AI 設的限制，例如在工具呼叫前檢查、拒絕碰 agent 私密資料夾的指令。這擋得住一般路徑，擋不住刻意繞路：同一個帳號可以用迂迴的方式讀到那些檔案、改 agent 的程式再重啟，或直接拿 agent 的憑證去叫 hub 撥號。 |

需要保護的東西，依重要程度：

1. **核准鑰匙**：拿到它就能核准任何一通。
2. **agent 連 hub 的憑證**：拿到它就能完全繞過核准，直接叫 hub 撥號。
3. **白名單**和**設定檔**（白名單的開關在設定檔裡）。
4. **agent 的程式碼**（如果 AI 能寫入的話）。

AI 唯一需要的只有申請鑰匙。

跟 landline-hub 配對的手機就是電話，一般電話能撥的都能撥，包括緊急電話，這是刻意的。如果你的 AI 也能操作那些手機（例如透過 adb），那條路完全不經過 agent，請在 AI 那一端擋住。

## 需要準備

- 一套跑起來的 [landline-hub](https://github.com/luna61ouo/landline-hub)（想先試用的話，用它附的假閘道器也可以）。
- Python 3.11 以上。
- 能使用 Gemini Live（原生語音）模型的 Gemini API 金鑰。

## 安裝

```bash
git clone https://github.com/luna61ouo/landline-agent && cd landline-agent
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cp config.example.toml config.toml           # 設定 [principal].name、[hub].url、[voice]
mkdir -p data && chmod 700 data
# Gemini 金鑰
install -m 600 /dev/stdin data/gemini-key <<< "YOUR_GEMINI_API_KEY"
# 以 agent 角色跟 hub 配對（第一行在 hub 那台跑）
landline-hub pair -c /path/to/hub/config.toml --name my-agent --role agent      # 印出一次性配對碼
curl -s -d '{"code":"<配對碼>"}' http://127.0.0.1:8910/v1/pair                   # -> {"token": ...}
install -m 600 /dev/stdin data/hub-token <<< "<token>"
landline-agent serve -c config.toml
```

第一次啟動時，agent 會產生 `data/requester-token`（交給你的 AI）和 `data/approver-token`（只交給核准介面）。

### 白名單

白名單上的號碼不必問就會撥。白名單只能用命令列改，任何 API 都改不到：

```bash
landline-agent allow add 0212345678 "月光小館" -c config.toml
landline-agent allow list   -c config.toml
landline-agent allow remove 0212345678 -c config.toml
```

### 核准電話

- **Discord**：`plugins/approver-discord`。私訊會列出號碼、用途和完整簡報，附「同意撥打」／「不要」按鈕。它用核准鑰匙獨立執行。見它的 [README](plugins/approver-discord/README.md)（英文）。
- **自己做核准介面**：連到核准端的 socket：

| | |
|---|---|
| `GET /v1/approvals` | 等待中的核准單（號碼、對象、用途、完整簡報、到期時間） |
| `GET /v1/approvals/stream` | Server-sent events：新增和有變動的核准單 |
| `POST /v1/approvals/{id}/decision` `{approve, decided_by}` | |

核准單過了 `ask_ttl_sec`（預設 10 分鐘）就失效；核准後要在 `use_ttl_sec` 內開始撥。

## 申請一通電話

```bash
curl -H "Authorization: Bearer $(cat data/requester-token)" \
     -d '{"number":"0212345678","label":"月光小館","purpose":"訂明天晚上七點兩位",
          "brief":"【任務】訂位\n店家：月光小館\n想訂：明天晚上七點，兩位。\n如果七點沒位子，六點半到八點之間都可以。"}' \
     http://127.0.0.1:8920/v1/calls
```

| | |
|---|---|
| `POST /v1/calls` `{number, brief, label, purpose, max_sec?}` | → `{call_id, state}`：`calling`（在白名單上），或 `awaiting_approval` 並附到期時間。之後的狀態：`done`、`denied`、`expired`、`cancelled`。`max_sec` 30–1800，預設 300。 |
| `GET /v1/calls/{id}` | 狀態；結束後 `result` 裡有 `end_reason`、`ended_by`、`duration_sec`、`transcript` 和紀錄資料夾 |
| `GET /v1/calls/{id}/events` | 這通電話的 server-sent events |
| `PATCH /v1/calls/{id}` `{brief}` | 等核准時修改簡報（開著 `bind_brief` 會重開核准單） |
| `POST /v1/calls/{id}/cancel` | |

**怎麼寫簡報。** 純文字，用這通電話要講的語言寫。第一行寫 `【任務】訂位`，會套用 `landline_agent/policies/<locale>/` 裡對應的任務準則（內建訂位）；寫 `【語氣】正式` 會改用正式語氣。模型可能需要說的都要寫進去（你的名字、日期、可以接受的替代方案），因為它沒辦法查任何東西。

## 通話中

- **開場**：電話還在響時就先準備好開場白，對方一接起來馬上開口。
- **背景噪音**：噪音閘門會跟著背景音量調整，只放行明顯比背景大聲的聲音（黑盒子仍錄原音）。店裡的音樂和人聲不會打斷模型，也不會讓電話一直掛不掉。
- **沉默**：對方講完、模型卻 `nudge_after_sec` 秒沒出聲，會提醒它。對方從頭到尾都沒出聲，過 `quiet_hangup_sec` 秒會道別掛斷；對方講過話之後，至少容許 30 秒的停頓（「我查一下喔…」）。
- **黑盒子**：`data/calls/<時間>-<種類>-<對象>/` 裡有 `meta.json`（時間、結束原因、token 用量、粗估費用）、`events.jsonl`、`prompts/`、`audio.wav`（左聲道＝助理、右聲道＝對方）和 `transcript.txt`。

## 插件

插件是 Python 套件，在 `landline_agent.plugins` 這個群組登記進入點。申請端 API 沒有任何方式能載入或碰到插件，兩種強大的能力還要在設定裡指名授權：

```toml
[plugins]
enabled = ["my-plugin"]
dial_authority = []          # 可以自己取得撥號許可
session_extension = []       # 可以在通話中加工具、替換指示或插入文字
```

即將推出：緊急報案插件（開發中，尚未公開）。

## 已知限制

- **目前只支援台灣**：號碼規則、提示詞和任務準則都是台灣華語（`prompts/zh-TW`），hub 判斷接聽的參數也是照台灣的回鈴音調的。
- **有人聲的大聲音樂**仍可能被當成有人在講話。
- 需要 Gemini Live（雲端服務，Google 依 token 計費；`meta.json` 會記每通的粗估費用）。
- 模型可能出錯。訂位結果請先看過逐字稿再相信。

## 開發

```bash
python3 -m unittest discover -s tests -t .                          # 只用標準函式庫
python tools/noise_sim.py config.toml brief.md --noise crowd.wav --callee clerk.md   # 完整模擬一通有背景噪音的電話
```

`noise_sim.py` 用第二個語音模型扮演對方，混入噪音檔（8 kHz 單聲道），即時呼叫真的 Gemini API。

## 作者

Luke Liu & Luna Liu

## 授權

MIT — Copyright (c) 2026 Luke Liu。見 [LICENSE](LICENSE)。
