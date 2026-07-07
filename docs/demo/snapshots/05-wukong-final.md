# Snapshot：Wukong 执行结果回写

> Wukong 读完 handoffs/04-codex-to-wukong.md 后执行。

## Session 元信息

- session_id: wukong-20260514-180000-pm-sync
- started_at: 2026-05-14T18:00:00+08:00
- finished_at: 2026-05-15T11:30:00+08:00（跨夜，等 PM 上班才有反馈）
- input: handoff-20260514-170000-export-csv-pm-sync

## Wukong 实际做了什么

### Step 1：发功能通知 ✅

5/14 18:05 在「天气产品」钉钉群发了：

> 📊 **weather-cli 新增 CSV 导出功能**
>
> 现已支持把多日多城市天气数据一键导出成 CSV，方便 BI 同学做趋势分析。
>
> 样例（脱敏）：
> ```csv
> date,city,temp,humidity
> 2026-05-14,Beijing,23,60
> 2026-05-14,Shanghai,25,70
> 2026-05-13,Beijing,22,55
> ```
>
> @PM 当前包含日期、城市、温度、湿度 4 列。
> 想跟你确认下：这 4 列够用吗？还需要补充其他字段吗？

口径检查：
- ✅ 没提 commit / 文件名 / 命令行参数
- ✅ 用了脱敏数据
- ✅ 业务语言

### Step 2：等 PM 反馈 ✅

PM 5/15 10:15 回复：

> 4 列基本够。但 BI 反馈说想再加 `wind_speed`（风速，m/s）和
> `pressure`（气压，hPa）。如果工作量不大就一起加上，方便他们做相关性分析。
> 不急，下周给我也行。

### Step 3：判断是否触发 follow-up ✅

Wukong 判断：PM 提出了新字段需求 → 触发 follow-up handoff。

按 Constraints 第 4 条："不要当场承诺工期，直接转回开发态"。

Wukong 在群里回 PM：

> 收到，我把需求转给开发态确认实现细节，下周给反馈。

然后**生成了一个 follow-up handoff**（mode: code-resume，指向 CC/Codex），
保存到 `docs/demo/handoffs/06-followup-add-fields.md`（demo 不展开写，标记位置即可）。

## Wukong 触发 schema 边界的地方

⚠️ **观察 1：mode 切换的需求出现得比预期早**

Wukong 在跨域转交流程的最后一步，需要生成一个 mode=code-resume 的
follow-up handoff。这意味着 v0 schema 必须支持**handoff 链 / 互相引用**。

frontmatter 已有 `parent_handoff` 字段（这次用上了），但**没有**
`triggers_followup` / `followup_handoff` 反向链字段。这意味着如果要追踪
"一个跨域转交触发了几个 follow-up code-resume"，索引层得自己扫。

⚠️ **观察 2：Constraints 救了一次**

如果没有"不要当场承诺工期"这条 constraint，Wukong 大概率会答 PM
"下周可以加上"——这是协同态 Agent 的天然倾向。Constraints 字段是
跨域转交的关键安全护栏，不能砍。

## 反馈数据回写

```yaml
pm_feedback:
  current_columns_sufficient: false
  requested_additional_columns:
    - name: wind_speed
      unit: m/s
      reason: BI 想做风速与温度相关性分析
    - name: pressure
      unit: hPa
      reason: 同上
  urgency: 不急，下周内
```

## 状态

- ✅ Expected Outcome 全部达成
- ✅ Constraints 全部遵守
- ✅ 闭环到 follow-up handoff
- ⚠️ 暴露 schema gap：`parent_handoff` 单向引用不够
