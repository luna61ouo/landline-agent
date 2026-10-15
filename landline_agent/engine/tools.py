"""The only tools the phone model gets by default.  No lookups: whatever it
could look up, it could say to the stranger on the other end."""

END_CALL = {"name": "end_call",
            "description": "掛斷電話。說完道別的話之後呼叫，呼叫後就不要再說任何話。"}
WAIT_FOR_HUMAN = {"name": "wait_for_human",
                  "description": "對方說「稍等」「我幫你轉接」「我去問一下」、或進入等待音樂時呼叫。"
                                 "呼叫後你說的話對方都聽不到，直到對方或下一個真人開口，你才會重新被聽到。"}
PRESS_KEYS = {"name": "press_keys",
              "description": "在電話上按按鍵（語音選單用）。digits 只能包含 0-9、*、#；字元 w 代表停 0.5 秒。"
                             "例：選單說「國語請按 1」就傳 \"1\"。按完不用說話，等選單或真人的下一句。",
              "parameters": {"type": "OBJECT", "properties": {"digits": {"type": "STRING"}}, "required": ["digits"]}}

DEFAULT_TOOLS = (END_CALL, PRESS_KEYS, WAIT_FOR_HUMAN)
