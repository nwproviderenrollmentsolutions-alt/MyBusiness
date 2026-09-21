# Avatar profile

This file is the source of truth for the locked AI clone identity. The AI UGC Viral
Cloner refuses to generate a presenter until `identity_lock.enabled` is `true`,
`identity_lock.replacement_allowed` is `false`, `avatar_identity.name` is filled in, and
at least one file has been added under `reference-images/` or `reference-video/`.

```yaml
avatar_identity:
  name: ""

identity_lock:
  enabled: true
  replacement_allowed: false

appearance:
  face: ""
  hair: ""
  skin: ""
  eyes: ""
  facial_features: ""
  body_type: ""
  age_appearance: ""

wardrobe:
  default: ""
  approved_variations: []

voice:
  provider: ""
  voice_id: ""
  characteristics: ""

personality:
  energy: ""
  speaking_style: ""
  facial_expression_style: ""
  gesture_style: ""

camera:
  preferred_framing: ""
  preferred_angles: ""
  preferred_lens_style: ""

negative_constraints:
  - different person
  - different face
  - altered identity
  - random influencer
  - stock actor
  - celebrity likeness
```
