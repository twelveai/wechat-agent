# Dashboard Page Overrides

> **PROJECT:** WeChat Agent Dashboard
> **Generated:** 2026-05-14 00:23:16
> **Page Type:** Dashboard / Data View

> ⚠️ **IMPORTANT:** Rules in this file **override** the Master file (`design-system/MASTER.md`).
> Only deviations from the Master are documented here. For all other rules, refer to the Master.

---

## Page-Specific Rules

### Layout Overrides

- **Max Width:** 1120px (operational dashboard)
- **Layout:** Calm two-column workbench on desktop; single column on tablet/mobile
- **Sections:** 1. Quiet header, 2. KPI strip, 3. Conversation workbench, 4. Support panels

### Spacing Overrides

- **Content Density:** Medium — dashboard remains scannable without visual noise

### Typography Overrides

- No overrides — use Master typography

### Color Overrides

- **Strategy:** Minimalist: Brand + white #FFFFFF + accent. Buttons: High contrast 7:1+. Text: Black/Dark grey
- **Implementation Guardrail:** Use teal as the only dominant hue. Orange is reserved for the primary CTA only. Status colors should be muted and always paired with text/icon labels.

### Component Overrides

- No overrides — use Master component specs

---

## Page-Specific Components

- No unique components for this page

---

## Recommendations

- Effects: 150-200ms color/shadow micro-interactions only. Avoid gradients, neon, glass blur, and multi-color KPI cards.
- CTA Placement: Summary generation button in the workbench toolbar
