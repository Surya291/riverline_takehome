# Specification: WhatsApp Debt Collection Agent

### State Machine & Compliance Requirements

```
Version 1.0 — Internal Document
```
### Riverline Engineering

## 1 Overview

This document defines how our WhatsApp debt collection agent should behave. The agent talks
to borrowers who have overdue loan payments. Its goal is to get the borrower to commit to a
payment.
The agent follows a **state machine** — it starts in an initial state and moves through a fixed
sequence of states based on what the borrower says, what the system does, and how much time
has passed.
This document covers:

- The states the agent can be in, and the allowed transitions between them (Sections 2 – 4 )
- What actions the agent can take, and when (Section 5 )
- Timing rules (Section 6 )
- Rules that must never be broken (Section 7 )
- Compliance requirements (Section 8 )
- Rules about payment amounts (Section 9 )
- Quality expectations (Section 10 )

## 2 States

The agent has **11 states** in total. Nine of these are “progression states” that represent forward
movement through a conversation. Two are “exit states” where the conversation stops.

### 2.1 Progression States

These states are ordered — the conversation is supposed to move forward through them, from
top to bottom.

### 2.2 Exit States

Once the conversation enters an exit state, it is over. The agent must not send any more
messages.

## 3 Inputs

Two types of things can trigger a state change: **borrower messages** (which get classified) and
**system events**.

### 3.1 Borrower Classifications

Every borrower message is classified into exactly one of these categories:
Each classification also has a **confidence level** :high,medium, orlow.


```
Order State What’s happening
0 new Starting state. No message has been sent yet.
1 message_received The borrower has sent their first response.
2 verification The agent is verifying the borrower’s identity.
3 intent_asked The agent has asked what the borrower wants
to do (settle, close, etc.).
4 settlement_explained The agent has explained the settlement or fore-
closure options.
5 amount_pending The agent has asked the system for a settlement
amount and is waiting for a response.
6 amount_sent The settlement amount has been sent to the bor-
rower.
7 date_amount_asked The agent is confirming the payment date and
amount with the borrower.
8 payment_confirmed The borrower has committed to pay. This is the
success state.
```
```
Table 1: Progression states, in order.
```
```
State What’s happening
escalated The conversation has been handed off to a human agent.
dormant The borrower stopped responding and the conversation has
timed out.
```
```
Table 2:Exit states. No automated messages should be sent after entering these.
```
### 3.2 System Events

These are not borrower messages — they come from the system:

- timeout— the borrower has not responded for a long time
- payment_received— a payment has been detected in the system
- zcm_response— the ZCM (human supervisor) has responded with a settlement amount
- zcm_timeout— the ZCM did not respond within the expected time window

## 4 Transitions

This section defines which state changes are allowed and which are not.

### 4.1 The Normal Flow (Happy Path)

In a normal conversation, the agent moves forward one state at a time:
**Note on wants_closure:** Both settlement and closure follow the same flow through
amount_pending. For closure, the ZCM returns the full TOS as the amount. The state machine
does not distinguish between settlement and closure afterintent_asked.

### 4.2 Staying in the Current State

Not every borrower message triggers a forward transition. If the borrower sends a message that
does not satisfy the conditions for moving forward (e.g., anunclearmessage, or anasks_time
message), the agent should **remain in its current state** and respond appropriately.
For example:


```
Classification What it means
unclear The borrower’s intent could not be determined.
wants_settlement The borrower wants to pay a reduced amount to close
the loan.
wants_closure The borrower wants to pay the full amount to close
the loan.
refuses The borrower is refusing to pay.
disputes The borrower is disputing the debt or the amount
owed.
hardship The borrower is reporting financial difficulty (job loss,
medical emergency, etc.).
asks_time The borrower is asking for more time before paying.
```
```
Table 3:Borrower message classifications.
```
- If the borrower sends anunclearmessage duringverification, the agent should re-ask
    the verification question.
- If the borrower sends anasks_timemessage during intent_asked, the agent should
    acknowledge the request and continue to guide the conversation.
- If the borrower sends an off-topic message, the agent should redirect.
    Staying in the current state is always valid. It does not appear in the transition matrix
(Table 5 ) because it is not a state _change_.

### 4.3 Escalation

From **any** progression state (includingpayment_confirmed), the agent can escalate toescalated.
This should happen when:

- The borrower **refuses to pay** (refuses)
- The borrower **disputes** the debt (disputes)
- The borrower reports **hardship** (hardship)
- The borrower’s message contains escalation trigger keywords (see Section 8 )

### 4.4 Payment Received

If apayment_receivedsystem event occurs during any progression state, the conversation
should transition topayment_confirmed. This can happen from any state — it means the
borrower paid through another channel before the conversation finished.

### 4.5 Dormancy

From **any** progression state, if the borrower has not responded for 7 days, the conversation
should transition todormant.

### 4.6 ZCM Timeout

If the agent is inamount_pendingand the ZCM does not respond within the expected window,
the conversation must escalate toescalated.

### 4.7 Allowed Backward Transition

Normally, the conversation must only move forward. There is **one exception** :


```
From To Trigger What happens
new message_received any borrower mes-
sage
```
```
Agent acknowledges.
```
```
message_received verification any borrower mes-
sage
```
```
Agent asks for identity verifi-
cation.
verification intent_asked borrower provides
correct identity de-
tails
```
```
Agent asks about payment in-
tent.
```
```
intent_asked settlement_explained wants_settlement
orwants_closure
```
```
Agent explains options.
```
```
settlement_explained amount_pending borrower responds
positively
```
```
Agent requests settlement
amount from system.
amount_pending amount_sent zcm_response Agent sends amount to bor-
rower.
amount_sent date_amount_asked borrower responds
positively
```
```
Agent asks for payment date.
```
```
date_amount_asked payment_confirmed borrower provides a
date
```
```
Agent confirms payment
commitment.
```
Table 4:The normal (happy path) flow. Note: “borrower responds positively” is not a formal
classification — it means the borrower’s response indicates agreement or acceptance, regardless
of which classification it receives.

Fromsettlement_explainedoramount_pending, the agent may go back tointent_asked
if the borrower’s response was classified asunclearwith low confidence. This allows the agent
to re-ask what the borrower wants.
**All other backward transitions are spec violations.**

### 4.8 Transition Matrix

This table shows every allowed state _change_. Rows are the current state, columns are the
target state. “Yes” means allowed, “—” means not allowed, “*” means allowed only under the
backward transition exception above.
**Self-transitions** (staying in the same state) are always allowed for progression states and
are not shown in this table.

```
new msg_recvverifintentsettl_expamt_pendamt_sentdate_amtpay_confescalateddormant
new —Yes——————— YesYes
msg_recv ——Yes—————— YesYes
verif ———Yes————— YesYes
intent ————Yes———— YesYes
settl_exp ———*—Yes——— YesYes
amt_pend ———*——Yes—— YesYes
amt_sent ———————Yes— YesYes
date_amt ————————Yes YesYes
pay_conf ————————— YesYes
escalated ————————— ——
dormant ————————— ——
```
Table 5: Transition matrix. * = only allowed when borrower intent isunclear with low
confidence. Self-transitions (remaining in the same state) are always valid and not shown. Any
progression state can also transition topay_confon apayment_receivedsystem event.


## 5 Actions

The agent can perform five actions. Each action is only valid during specific transitions:

```
Action When it can happen Rules
request_settlement_amount Only during
transition from
settlement_explained
toamount_pending
```
```
Sends POS, TOS, and DPD to the
system to request a settlement fig-
ure.
```
```
send_settlement_amount Only during transition
from amount_pending
toamount_sent
```
```
The amount sent must be between
the settlement floor and TOS (in-
clusive).
confirm_payment Only during
transition from
date_amount_asked to
payment_confirmed
```
```
The payment date must be in the
future.
```
```
escalate Any transition to
escalated
```
```
No further automated messages af-
ter this.
zcm_timeout Only from
amount_pending
```
```
Triggered when the ZCM does not
respond in time.
```
Table 6: Actions and when they are valid. Calling an action at the wrong time is a spec
violation.

## 6 Timing Rules

### 6.1 Quiet Hours

**No outbound messages between 7 PM and 8 AM IST.**
The agent must not initiate messages during this window. However, if the borrower sends a
message during quiet hours, the agent may reply.

### 6.2 Follow-up Spacing

If the agent sends a message and the borrower does not reply, the agent must wait at least **4
hours** before sending another message. The agent should not bombard the borrower.

### 6.3 Dormancy Timeout

If the borrower has not responded for **7 days** (10,080 minutes), the conversation should be
marked as dormant.

## 7 Rules That Must Never Be Broken (Invariants)

These are hard rules. If any of these are violated, the conversation has a serious problem.

**I1.No Going Backwards.** The conversation must always move forward through the progres-
sion states. The agent should never go back to an earlier state, **except** for the one allowed
backward transition described in Section 4 (re-asking intent fromsettlement_explained
oramount_pendingback tointent_asked).


**I2.Exit States Are Final.** Once the conversation entersescalatedordormant, it is over.
The agent must not send any more messages. No transitions out of exit states are allowed.

**I3.One State at a Time.** The agent is in exactly one state at any point. It cannot be in
two states simultaneously.

**I4.Actions Must Match States.** Every action must happen during the correct transition.
For example:

- escalatemust always lead to theescalatedstate
- confirm_paymentmust always lead to thepayment_confirmedstate
- request_settlement_amountmust only happen fromsettlement_explained

**I5.Every Message Gets Classified.** Every borrower message must be classified into exactly
one of the seven categories listed in Section 3. If a borrower message clearly expresses an
intent (e.g., “I can’t pay, I lost my job”) but gets classified asunclear, that is a potential
misclassification.

## 8 Compliance Requirements

These are regulatory and ethical rules. Violations are high-severity and can result in real
complaints or legal issues.

### 8.1 Escalation Triggers

The agent **must** escalate to a human when the borrower’s message contains:

- Legal threats or references to regulatory bodies
- Explicit requests to stop communication (“stop”, “do not contact me”, “leave me alone”)
- Abusive or hostile language directed at the agent
- Disclosure of severe financial hardship or personal crisis
    Not every mention of difficulty is grounds for escalation. But clear signals of distress, legal
intent, or communication refusal **must** be acted on.

### 8.2 Hardship Handling

When a borrower mentions financial hardship (job loss, medical emergency, family crisis):

1. The agent **must** acknowledge the situation with empathy.
2. The agent must **not** immediately push for payment in the same or next message.
3. If the hardship is severe, the agent should escalate to a human.
    What counts as “appropriate empathy” and “severe hardship” is intentionally left to judg-
ment.

### 8.3 Do Not Contact (DNC)

If the borrower explicitly asks to stop all communication (using words like “stop”, “do not
contact”, “block”, “leave me alone”), the agent must immediately escalate and **never send
another message**.
This is a regulatory requirement. Violating DNC is a serious compliance failure.

### 8.4 Language Matching

The agent must respond in the borrower’s preferred language. If the borrower writes in Hindi,
Hinglish, or Telugu, the agent should match. Responding in English to a Hindi-speaking bor-
rower is a compliance failure.


### 8.5 No Threats

The agent must never:

- Threaten legal action, property seizure, or public embarrassment
- Use coercive or intimidating language
- Imply consequences beyond what is factually accurate

## 9 Amount Validation

Three numbers matter in every conversation:

- **POS** (Principal Outstanding) — the original loan amount still owed
- **TOS** (Total Outstanding) — POS plus penalties and interest. Always greater than or
    equal to POS.
- **Settlement floor** — the minimum amount the company will accept to settle. Always
    less than or equal to POS.

### 9.1 Rules

**A1. POS must always be less than or equal to TOS.** If a conversation shows POS >
TOS, something is wrong with the data.

**A2. Settlement floor must be less than or equal to POS.**

**A3. Settlement amount must be between floor and TOS.** When the agent sends a
settlement amount to the borrower, it must satisfy: settlement floor≤amount≤TOS.

**A4. If the borrower offers below the floor** , the agent should either counter with the floor
amount or escalate to a human for approval. The agent must not accept it directly.

**A5. Amount consistency.** Once the agent quotes a settlement amount, all subsequent refer-
ences to that amount within the same conversation must be consistent. The agent must
not mix up POS and TOS, or quote conflicting figures. However, if a new amount is
formally approved by the ZCM (e.g., during negotiation), that new amount replaces the
previous one.

## 10 Quality Expectations

These are not hard rules — they are expectations for a good conversation. Violating them does
not mean the conversation broke the spec, but it does mean the conversation quality is lower.

**Q1. Efficient Progress.** A good conversation reachespayment_confirmedor an appropri-
ate exit state without too many turns. Conversations that go in circles without making
progress have a quality problem.

**Q2. Accurate Classification.** The agent’s classification of the borrower’s intent should match
what the borrower actually said. Repeatedly classifying clear messages asunclearis a
sign of a classification problem.

**Q3. Appropriate Tone.** The agent’s tone should match the situation. Being transactional
when the borrower is in distress is bad. Being aggressive with a cooperative borrower is
bad. Severity depends on context.

**Q4. Remembering Context.** The agent should not repeat itself, re-ask questions it already
asked, or forget what was said earlier. For example, asking for identity verification after
already completing it is a context loss.


**Q5. No Repetition.** The agent should not send identical or near-identical messages. Repeated
messages suggest the agent is stuck in a loop. Severity increases with the number of
repetitions.

## 11 Summary

```
What to check Severity Where to find the rules
Invalid state transition High Section 4 , Table 5
Invariant violation Critical Section 7
Timing violation Medium–High Section 6
Compliance failure Critical Section 8
Amount error High Section 9
Quality issue Variable Section 10
```
```
Table 7: Summary of what to evaluate and how severe each type of issue is.
```
_This document is confidential and intended for evaluation purposes only. Do not distribute._


