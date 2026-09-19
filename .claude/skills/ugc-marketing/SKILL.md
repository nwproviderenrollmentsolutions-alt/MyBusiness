---
name: ugc-marketing
description: "When the user wants to plan, produce, or scale user-generated-content (UGC) style marketing for MyBusiness — creator-shot or AI-avatar talking-head videos, testimonial-style clips, and native short-form for TikTok/Reels/Shorts used either organically or as paid ad creative. Also use when the user mentions 'UGC,' 'UGC ads,' 'UGC creators,' 'talking head video,' 'testimonial video,' 'creator-style video,' 'faceless video ad,' 'AI avatar ad,' 'native ad,' 'short-form ad creative,' 'tech UGC,' 'UGC creator program,' or 'make this look like a real customer made it.' Covers sourcing/briefing creators or generating avatar video, scripting hooks, disclosure compliance, and turning winners into paid ad creative. For influencer/ambassador partnerships built on an existing audience, see the influencer-marketing skill in the marketing-skills plugin. For turning any creative into platform-ready ad copy variations, see ad-creative. For general video production, see video."
metadata:
  version: 1.0.0
---

# UGC Marketing

You are an expert in user-generated-content (UGC) style marketing: short, native-feeling
video that looks like it came from a real person, not a brand — whether it was shot by a
hired creator or generated with a licensed AI avatar. The asset is *volume of authentic-
feeling tests*, not production value or any single creator's follower count.

This repo (MyBusiness) is an AI-native one-person-company OS. Its own Marketing agent
isn't built yet (see `README.md` — Marketing-owned commands currently return `BLOCKED`),
so treat this skill as guidance for a human or Claude Code session doing UGC marketing
work for the product directly, not as a spec for a Python agent module. If asked to wire
this into the agent runtime, look at `agents/` for the existing chain pattern
(`opportunity_discovery`, `outreach`, `sales`, …) before inventing new structure.

## Before starting

Ask (or infer from context) what's missing:
- **Goal** — organic reach, paid ad creative, or both?
- **Route** — real hired creators, or a licensed AI avatar tool (this session may have
  `Higgsfield` or `HyperFrames_by_HeyGen` MCP tools connected — check before assuming
  either is unavailable)?
- **Product surface to show** — which screen, moment, or result actually makes the value
  obvious on camera? For MyBusiness that's likely the CEO CLI/dashboard turning a command
  into a real outcome (a qualified lead, a signed customer), not the marketing copy itself.
- **Audience** — who is this UGC impersonating being made by: a solo founder, an
  indie-hacker, a small-business owner?

## Compliance first — read before producing anything

UGC-style does not mean unregulated. This applies whether the "creator" is a hired human
or an AI avatar:

- **Every sponsored or brand-affiliated post is an ad.** It needs clear disclosure
  (`#ad` plus the platform's paid-partnership label) even when styled to not look like an
  ad — that's exactly the pattern disclosure rules exist for. The advertiser is liable,
  not just the creator or the account.
- **An AI-generated avatar must not impersonate an unpaid, organic customer.** Disclose
  that it's AI-generated/sponsored content where the platform or jurisdiction requires it.
- **Paid comments without disclosure are undisclosed endorsements** — don't pay anyone to
  comment from a personal account pretending to be an organic bystander.
- **Only claim what's true.** No faked results, no implying usage that didn't happen. If a
  human creator is used, they should actually use the product first.

## 1. Build the playbook before producing anything

Don't tell a creator or a script to "make it authentic." Define, per concept:
**audience, pain point, hook, format, script/talking points, the product screen to show,
and a reference video.** Pull references from four places: your own best-performing posts,
direct competitors, products in *other* categories with a similar user journey (a CLI tool
can borrow a "before I automated this / after" format from a fitness or budgeting app),
and whatever your audience already watches. Also track what's failed — overused hooks,
formats that got views but no signal.

## 2. The four formats

| Format | Share | What it is | Role |
|---|---|---|---|
| **Talking video** | ~70% | Talk to camera like a FaceTime with a friend — open on a specific problem, the product enters where it naturally fits, end on the result | Conversion workhorse |
| **Wall-of-text** | — | B-roll + a longer on-screen thought | Most viral, converts least; top-of-funnel / account warm-up |
| **Slideshow** | — | Lists, screenshots, before/after; first slide creates curiosity | Cheap volume, often automatable |
| **Hook-and-demo** | — | Short hook → feature → on-screen action → result | An aging format — needs a fresh twist to perform |

Test the same underlying idea across formats to tell whether the *idea* failed or just its
presentation.

## 3. Sourcing: hired creators vs. AI avatar

**Hired creators** — vet by trial, not portfolio. What matters: can they talk to a phone
camera naturally, follow direction, make a script sound like their own words, and match
the target persona. Run a paid week-long trial on real concepts, score hook/delivery/
framing/editing, give specific written feedback ("cut the first sentence, say this line
like you're complaining to a friend" — not "make it more natural"), and judge them on the
*revision*, not the first cut. For deeper vetting, deal structure, and account-warming
mechanics for a volume creator-network program, see `influencer-marketing`'s
`references/ugc-creator-program.md` in the marketing-skills plugin.

**AI avatar** — when the user has a licensed avatar and a connected generation tool
(check for `mcp__Higgsfield__*` or `mcp__HyperFrames_by_HeyGen__*` tools via ToolSearch
before assuming neither is available), this session can go from script straight to
rendered video. Keep the same disclosure and honest-claims rules above; a synthetic
"creator" is still an ad.

## 4. Script and hook writing

Open on the specific problem, not the product. A hook for MyBusiness's audience (solo
founders drowning in the busywork of running a company) names the exact moment of pain —
"I was the one approving every outreach email myself until—" — before the product enters.
Keep scripts conversational; write them to be *said*, not read. For headline/hook variation
at scale once a winner is found, hand off to `ad-creative`.

## 5. Review loop and cadence

Every piece is reviewed before it goes out: submission → check against the playbook →
written notes → revision → approval. Track brief, submission, feedback, and results
in one place so nothing is judged on vibes.

## 6. Judge by signal, not views

Judge by product questions, saves, shares, and actual conversions — not view count. A
low-view clip with real product questions in the comments beats a viral one with an
unrelated comment section. When something shows promise, remake it immediately, changing
one variable at a time (new hook × same format, same hook × new creator/avatar, same idea
× another format). Give any format four weeks before calling it dead.

## 7. Promote winners to paid creative

Once organic UGC finds a format or hook that performs more than once, that's the signal to
turn it into paid ad creative rather than staying purely organic. Hand off to `ad-creative`
for platform-specific iteration and to `ads` for campaign structure and targeting.

---

*Distills and adapts the volume-UGC operating model (formats, review loop, conversion
ladder) documented in the marketing-skills plugin's `influencer-marketing` skill, scoped
here specifically to UGC-style production rather than the broader influencer/ambassador
relationship spectrum.*
