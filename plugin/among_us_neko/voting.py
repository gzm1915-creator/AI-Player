"""Bounded model decision requests; this module never chooses or casts a vote."""


class VoteReminder:
    interval = 15
    limit = 3

    def __init__(self):
        self.key = None
        self.attempts = 0
        self.last_request = None
        self.status = {"state": "waiting_meeting", "summary": "等待会议"}

    def observe(self, state, enabled, now):
        voting = state.get("voting") or {}
        key = (state.get("sessionId"), state.get("matchId"), state.get("meetingId"))
        if state.get("connected") and state.get("meetingOpen") and key != self.key:
            self.key, self.attempts, self.last_request = key, 0, None
        code, summary = (
            ("disabled", "自动投票决策已关闭") if not enabled else
            ("disconnected", "等待游戏连接") if not state.get("connected") else
            ("waiting_meeting", "等待会议") if not state.get("meetingOpen") else
            ("unsupported", "需加载0.6.86游戏组件以读取投票阶段与名单") if not voting.get("supported") else
            ("recorded", "游戏已记录本人投票") if voting.get("hasVoted") else
            ("ineligible", "本人当前不能投票") if not voting.get("localAlive") else
            ("submitted", "已提交，等待游戏记录投票") if voting.get("state") == "submitted" else
            ("waiting_phase", "等待游戏进入可投票阶段") if not voting.get("votingOpen") else
            ("waiting_gate", "等待讨论条件满足") if not state.get("voteGate", {}).get("ready") else
            ("waiting_gate", "当前暂不可投票") if not voting.get("canVote") else
            ("attention_required", "已提醒三次，AI仍未完成投票，请检查模型工具调用") if self.attempts >= self.limit else
            ("awaiting_decision", "等待AI选择玩家或跳过")
        )
        self.status = {"state": code, "summary": summary, "attempts": self.attempts,
                       "maxAttempts": self.limit, "reminderIntervalSeconds": self.interval,
                       "sessionId": state.get("sessionId"), "meetingId": state.get("meetingId")}
        remaining = voting.get("remainingSeconds")
        interval = 5 if type(remaining) in (int, float) and 0 < remaining <= 10 else self.interval
        self.status["reminderIntervalSeconds"] = interval
        return code == "awaiting_decision" and (self.last_request is None or now - self.last_request >= interval)

    def request(self, state):
        return {"required": True, "reason": "voting_phase_unvoted", "attempt": self.attempts + 1,
                "sessionId": state["sessionId"], "meetingId": state["meetingId"],
                "candidates": [p for p in state["voting"].get("candidates", []) if p.get("eligible")],
                "skipPlayerId": -1,
                "instruction": "现在必须做投票决策：根据本局目击和讨论选择一名合法玩家，或证据不足时选择跳过。调用among_us_vote或独立vote入口，传player_id和本次session_id、meeting_id；仅口头说要投票不算执行。先核对最新状态，旧会议决策失效。不要把本提醒发进游戏聊天，也不必为投票额外发言。submitted只表示提交，voting.hasVoted才表示游戏已记录本人投票。"}

    def delivered(self, now):
        self.attempts += 1
        self.last_request = now
        self.status["attempts"] = self.attempts
