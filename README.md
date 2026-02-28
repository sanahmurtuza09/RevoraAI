# RevoraAI — Reflection & Clinician Insight Prototype

Live Demo: https://revoraai.streamlit.app/

RevoraAI is a product prototype exploring how structured reflection, analytics, and AI-assisted summarization can improve therapy outcomes for autistic and neurodivergent adults.

It was built to answer a simple question:

How can we reduce time spent reconstructing context in therapy and increase signal clarity for both clients and clinicians?

---

## Problem

Many autistic and neurodivergent adults find it difficult to track emotions consistently or recall key experiences between sessions. This often leads to:

- Time spent reconstructing the week
- Repeated conversations
- Reduced session focus
- Slower perceived progress

On the clinician side, therapists spend valuable time reviewing notes and prompting recall instead of advancing care.

There is an opportunity to support both sides with structured reflection, clearer pattern visibility, and concise summaries that improve communication and outcomes.

---

## Product Hypothesis

If clients can easily log daily reflections and generate structured summaries that surface patterns and progress, they will feel more prepared and self-aware entering sessions.

If clinicians receive clear, range-based insights before sessions, they can spend less time reconstructing context and more time delivering personalized care.

---

## What This Prototype Includes

## Role-Based Experience Design
The prototype separates workflows intentionally:

**Check-In (Client View):** Designed for autistic and neurodivergent adults to log daily mood and event-based reflections in a simple, non-judgmental interface.
- Daily mood log (1–10 scale)
- Event based check-ins when something shifts
- Weekly AI-assisted reflection summary
- Simple, non-judgmental language

**Clinician Summary (Therapist View):** Structured, neutral summaries that surface trends, themes, and session prep insights.
- Date range filtered summary view
- Snapshot metrics (coverage, engagement, steadiness)
- Mood volatility insights
- Top themes extraction
- “Things to notice” pattern flags
- Simple emotions tracker chart
- AI-generated session prep notes and conversation starters with copy option
- PDF export for workflow integration

**Dashboard (Shared View):** A data visualization layer that can support both clients and clinicians with trend analysis and engagement metrics.
- Average daily mood (anchor metric)
- Active days (engagement depth)
- Total check-ins
- Event check-ins (signal density)
- Mood trend visualization
- Engagement over time

---

## Metrics & Accountability

The prototype tracks early product-level metrics:

- Engagement Rate (target: ≥4 entries/week)
- Active Days per User (target: ≥60% of possible days)
- Average Daily Mood (trend over time)
- Mood Steadiness (volatility indicator)

In a production setting, these would ladder up to outcome metrics such as:

- Reduction in clinician prep time
- Increased client-reported progress clarity
- Retention beyond 8 weeks
- Measurable mood improvement over time

This is intentionally outcome-oriented rather than feature-driven.

## Key Product Decisions

- Separated client and clinician views to reduce cognitive load.
- Used daily check-ins as the “anchor” signal for consistency metrics.
- Designed AI output to be editable before export (human-in-the-loop).
- Prioritized pattern surfacing over raw data display.

---

## AI in This Product

AI is used to:

- Generate structured weekly reflections
- Create clinician-ready summaries
- Suggest conversation starters

In the public demo, AI features are disabled to avoid exposing API keys.

To enable AI locally:
1. Create a `.env` file with:
OPENAI_API_KEY=your_key_here
2. Run the app locally (see below)

The intent is not to replace clinician judgment, but to augment preparation and surface patterns efficiently.

---

## If I Were Scaling This

If RevoraAI moved from prototype to production, I would focus on:

### 1. Defining a Clear North-Star Metric

Shift from engagement to outcome accountability. Examples:

- % improvement in self-reported mood over 8 weeks  
- Reduction in session time spent reconstructing context  
- Clinician-reported usefulness score per session  

Every roadmap decision would ladder up to moving this metric.

---

### 2. Longitudinal Outcome Tracking

Introduce structured trend analysis:

- Baseline vs. 30/60/90-day comparisons  
- Mood volatility trends  
- Correlation between engagement depth and outcome improvement  

---

### 3. Clinician Feedback Loop

Build direct signal from clinicians:

- “Was this summary useful?” rating  
- Quick structured feedback tags  
- Time-to-insight measurement  

Use this data to continuously refine summarization quality and signal extraction.

---

### 4. AI Companion (Chat Support Layer)

Introduce a structured AI reflection companion to:

- Help users articulate emotions
- Offer guided reflection prompts
- Encourage consistent engagement
- Provide emotional validation without clinical replacement

This would remain supportive, not diagnostic, and would require careful guardrails.

---

### 5. Responsible AI Guardrails

If AI were enabled at scale:

- Usage rate limits
- Clinician-in-the-loop review options
- Hallucination/error tracking
- Bias monitoring in generated language
- Clear disclaimers about non-diagnostic nature

AI should support care, not automate it blindly.

---

### 6. Role-Based Access & Data Boundaries

If this moved into production, I would introduce formal role-based access controls (RBAC):

- Clients see only their own data
- Clinicians see only assigned client panels
- Admin roles with limited data visibility
- Clear separation between personal reflection and clinician-facing summaries

Beyond permissions, this would include:

- Audit logs for data access
- Consent-based data sharing
- Session-level access control
- HIPAA-aligned storage architecture

In mental health care, trust is infrastructure.  
Role clarity and data boundaries are not just technical requirements, they are product requirements.

---

### 7. Intentional Notification System

In a production environment, I would introduce a notification system designed specifically to avoid overwhelm.

Principles:

- Opt-in by default
- Customizable frequency (daily, 3x/week, weekly)
- Tone aligned with neurodivergent-affirming language
- Smart suppression (pause after multiple missed prompts)
- Context-aware reminders (e.g., no late-night notifications)

Instead of pushing for maximum engagement, the goal would be:

Sustainable consistency without cognitive overload.

Over time, we could experiment with:

- Personalized reminder timing based on prior engagement
- “Low-friction” check-in prompts (1-click mood logging)
- Clinician-triggered reminders when appropriate


---

## Tech Stack

**Application Framework:** Streamlit (rapid prototyping, fast iteration)  
**Backend & Logic:** Python  
**Database:** SQLite (structured storage for longitudinal mood and event data)  
**Data Processing & Analytics:** Pandas  
**Visualization:** Altair, Matplotlib  
**AI Integration:** OpenAI API (LLM-powered summarization)  
**Reporting & Export:** ReportLab (clinician-ready PDF generation)  
**State Management:** Streamlit Session State  

---

## Why This Exists

Autistic and neurodivergent adults have historically been underserved in traditional care models.

This prototype explores how thoughtful product design, measurable outcomes, and responsible AI integration can:

- Improve clarity
- Reduce clinician cognitive load
- Increase session effectiveness
- Support neurodivergent-affirming care

It was built to explore product questions, not just demonstrate functionality.

## What This Taught Me

Building this prototype reinforced that in neurodivergent care:
- Simplicity outperforms feature density.
- Engagement must be sustainable, not aggressive.
- AI should augment human care, not replace clinical judgment.