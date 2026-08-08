# Original robot face source

`original-robot-expression-sheet-v1.png` is the immutable source sheet for the
custom Stack-chan face pack. It was generated specifically for this project
with the built-in OpenAI image generation tool on 2026-08-07 and does not use
the vendor UI, community Stack-chan avatars, or an existing character design.

The source contract is a 3×4 expression grid of one original cream-and-lavender
robot mascot. `scripts/build_face_assets.py` applies fixed 320×240 crops without
resizing, then embeds the optimized PNG files into firmware flash.

Expression order:

1. neutral, happy, listening
2. thinking, speaking soft, speaking excited
3. surprised, sleepy/blink, shy
4. worried, playful, petted

The final image-generation prompt specified a gender-neutral round robot spirit,
cream-white face shell, lavender ear modules, violet star-highlight eyes, peach
LED blush, mint accents, a flat `#F5F3FA` background, fixed face geometry, and
expression-only changes to eyes, eyebrows, blush, and mouth. It prohibited
text, logos, watermarks, bodies, props, copied mascots, and existing characters.
