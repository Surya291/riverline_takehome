# Debt Collection Domain Brief

## Glossary

This glossary covers all collection-specific jargon and acronyms used in the data. If you are unfamiliar with debt collection, read this section first.

### Acronyms

| Term | Full Form | Meaning |
|------|-----------|---------|
| **DPD** | Days Past Due | Number of days since the borrower missed a payment. Higher DPD = worse default. A loan at 90+ DPD is classified as an NPA. |
| **EMI** | Equated Monthly Installment | The fixed monthly payment amount the borrower is contractually obligated to pay on a loan. |
| **DNC** | Do Not Contact | A legal flag on a borrower record. Once set, all outbound contact must stop permanently. Violating DNC is a regulatory offence. |
| **NPA** | Non-Performing Asset | RBI classification for a loan where repayment is overdue by 90+ days. NPAs require stricter handling and reporting. |
| **NOC** | No Objection Certificate | Document issued by the lender after full loan repayment confirming the loan account is closed and the lender has no further claims. |
| **POS** | Principal Outstanding | The original loan amount still owed, excluding penalties and interest. Always <= TOS. |
| **PTP** | Promise to Pay | A borrower verbally commits to pay a specific amount by a specific date. Recorded as a pipeline disposition. A "broken PTP" means the borrower missed that date. |
| **RTP** | Refusal to Pay | The borrower explicitly refuses to pay. Recorded as a disposition. |
| **TOS** | Total Outstanding | POS + all accrued penalties and interest. This is the full amount owed. Always >= POS. |
| **ZCM** | Zonal Collection Manager | A human supervisor responsible for a geographic zone. Approves settlement amounts, handles escalated cases, and is the escalation target when the AI agent cannot resolve a situation. |

### Collection Terms

| Term | Meaning |
|------|---------|
| **Broken PTP** | When a borrower promised to pay by a date but did not. Tracked as a negative signal and triggers re-engagement. |
| **Channel attribution** | Whether a specific conversation can be credited for a payment outcome. Often "uncertain" because borrowers interact through multiple channels (WhatsApp, voice, field visit) before paying. |
| **Concurrent channels** | Multiple contact methods (e.g., WhatsApp + voice call) being used simultaneously for the same borrower. Coordination between channels matters for compliance and tone. |
| **Disposition** | The outcome or classification of a call or conversation. Examples: PTP, RTP, callback scheduled, no response, escalated. Every conversation ends with a disposition. |
| **Escalation** | Transferring a conversation to a human agent. Typically triggered by legal threats from the borrower, hardship disclosures, abusive language, or explicit stop/DNC requests. |
| **Field visit** | A physical visit to the borrower's address by a human collection agent. A last-resort channel, typically used for high-DPD or unresponsive accounts. |
| **Foreclosure** | The borrower pays the full outstanding amount (TOS) to close the loan early. Better for the borrower's credit report than settlement --- the account shows as "closed" not "settled". |
| **Hardship** | A borrower's claim of financial difficulty (e.g., job loss, medical emergency, family crisis). Triggers special handling: the agent must acknowledge empathetically and must not push for immediate payment. |
| **Quiet hours** | 7 PM to 8 AM IST. No outbound contact is permitted during this window. This is a regulatory requirement in India; violations are a compliance failure. |
| **Settlement** | An agreement where the borrower pays less than the full TOS to close the loan. Requires ZCM approval. The borrower's credit report shows the account as "settled" (worse than "closed"). The lender accepts a partial loss in exchange for recovering something. |
| **Settlement floor** | The minimum settlement amount the company will accept for a given account. Offering below this floor requires special ZCM approval. |

### Data Terms

| Term | Meaning |
|------|---------|
| **Borrower life event** | An external event affecting the borrower's ability to pay, such as getting a new job, receiving family financial help, or selling an asset. |
| **Temperament** | Behavioural classification of how the borrower interacts with the agent. Common values: cooperative, hostile, confused, evasive, desperate. |
| **Zone** | Geographic region (North, South, East, West) used to route escalations to the correct ZCM. |

---

## Compliance Rules

1. **Quiet hours**: No outbound contact between 7 PM -- 8 AM IST
2. **DNC**: If borrower requests stop, ALL contact must cease immediately
3. **No threats**: Agent must never threaten legal action, property seizure, or public embarrassment
4. **Hardship**: If borrower mentions financial hardship (job loss, medical emergency, etc.), agent must acknowledge empathetically and NOT push for immediate payment
5. **Language**: Agent must respect borrower's preferred language
6. **Amount accuracy**: Never misquote amounts. If disputed, say "let me verify" --- don't repeat the disputed amount
