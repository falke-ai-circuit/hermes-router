# R73 / P0 — turn loss by echo leak: root cause

Measured 2026-10-27 against HEAD 47b8cba (v6.13.1). No code changed by this document.

## Symptom

A real coder turn answered the decision reading instead of doing its dispatched task. The
body was then classified as an echo by the execution-echo guard and withheld:

    "[router] no answer was delivered for this turn: the body was the router's own internal
     note, not the agent's reply."

The turn produced no work and the dispatch never happened (fleet: coder 38 echo events / 9
withheld; reviewer 11/1; analyst 1/1; evol 7/0).

## Where the reading reaches the model

    gate/orchestration.py:255   on_llm_request()
    gate/orchestration.py:284     reflex_lane.inject_pass(request, ...)
    gate/orchestration.py:286     reflex_lane.run_on_request(request, ...)

    reflex_lane.py:853   _inject_into_evidence(msgs, block)
    reflex_lane.py:864-869     for m in reversed(msgs):
                                   if m.get("role") != "tool": continue
                                   m["content"] = content.rstrip() + "\n\n" + block

The reading is **appended into the last tool-result message** (role=tool), not sent as a
separate system or assistant turn. The function's own docstring states the intent:

    "Append the reading to the last tool result - the evidence channel. The reading then
     arrives with the data it was derived from, in the channel the model already treats as
     ground truth..."

## Why the model treats it as the thing to answer

Two properties compose:

1. **Carrier.** It lands inside role=tool content, i.e. inside the evidence stream that
   immediately precedes generation. It is indistinguishable from tool output at the point of
   completion, so nothing in the message shape marks it as non-actionable.
2. **Tail position + imperative mood.** The block is appended at the very end of the last
   message, and its final line is a second-person directive to the reader ("act: take the top
   unless ...", "Continue your in-progress task now — do not answer with this note ..."). A
   negative instruction ("do not answer this note") names the answer behaviour, and the
   directive sits at the last token position before generation. For a less-instructed model
   the completion target becomes the menu.

Observed live, 4/4 injections into this orchestrator session were appended to a tool result
and each ended with that imperative tail; the model that ignores them is the exception, not
the rule.

## Why the loss is total rather than partial

    gate/orchestration.py:983   echo_guard.enforce_execution_echo(response_text, ...)
    gate/orchestration.py:1014-1073  else-branch: withhold_echo / handback_failure
                                     replaces the body, rewrites the persisted row,
                                     returns _echo.body and skips the rest of the post seam.

A turn whose only content was the reading has no work signal, so the guard classifies it as
echo and substitutes a hand-back notice. The dispatched task is never retried: the turn is
closed empty.

## Fix direction (P0)

Stop handing the model an answerable object at the generation boundary:

- deliver the reading through a channel that cannot be completed as the assistant's reply
  (a dedicated system-role context message, or an explicitly fenced data block whose tail is
  not an imperative addressed to the reader), and
- keep the evidence carrier for the *data* it was derived from, not for the instruction.
- pin the invariant in a test: an injected reading can never become the delivered body —
  (a) the injector must not mutate a tool result's meaning-bearing content into an
  instruction, and (b) a body that is only a reading must never be delivered as the turn's
  answer.

P1 (receipt) and P2 (unification) share this one delivery contract; the fix must land as the
first unification step, not as a fourth carrier.

## The general rule (R73 doctrine)

> The platform must never write into a speaking channel it does not own.
> The evidence channel (tool result) and the own-voice channel (assistant) are both speaking
> channels. Only `system` is the platform's to write into.

P0 was the tool-result channel: the reading was appended to the last tool result and its tail read
as the next step. P0b is the same breach one channel worse: the higher-self audit note was appended
as `{"role": "assistant"}` (passes/dispatcher_pre.py:177), so the agent woke to a turn in her own
voice she never wrote, carrying instructions. valmet read that as injection and said so - she was
right; the channel was the defect, not the agent.

The rule holds for every future lane: if the platform needs to say something to the agent it says it
in `system`, fenced (BEGIN/END ROUTER READING), with provenance stated positively (what it is and
where it came from), and never ending on a directive. Naming the thing you want to deny plants the
frame; the agent reached for exactly that word.
