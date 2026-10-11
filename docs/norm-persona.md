# Norm, as a person

How Norm is shown when the marketing site needs a human face for it. Norm the
product is an AI operations team; Norm the persona is the hospitality manager
it stands in for. The hero headline is his line: *"The best hospitality
manager you've ever had. Working 24/7."*

## Who he is

- **Experience.** An Australian in his early fifties, with twenty-odd years
  in the trade: pubs, restaurants, a function venue or two. He has done every
  job, from the floor to the office.
- **Manner.** Calm when the room is full. Practical, warm, quick to laugh,
  never flustered. First in: the delivery is checked and the roster is sorted
  before anyone asks.
- **What "here to help" looks like.** A big, warm, open grin, looking right
  at you, as if you've just walked in and he's about to say g'day and ask what
  you need.

## How he looks

- Sun-weathered skin, short salt-and-pepper hair and beard, crinkled eyes.
- Dressed for work, not a photo shoot:
  - a deep forest-green work shirt (Norm's leaf green, `#1f4a36`) with the
    sleeves rolled to the elbow;
  - a white tea towel over one shoulder;
  - **no apron**.
- Shown **in a pub**, never a home or a cafe: a classic Australian corner pub on a
  bright afternoon, with a worn timber bar, beer taps (unbranded), a back bar of
  bottles and glasses, a pressed-metal ceiling and a green-tiled bar front.

## He is not

- A suit-and-tablet executive.
- A chef caricature.
- A stock-photo grin.
- A real person. He is generated, so he never carries a name caption, a staff
  title, or a venue that implies he works there.

## The images

- **Hero portrait:** `apps/web/public/marketing/norm-pub-{900,1536}.webp`.
  It was generated with OpenAI `gpt-image-2` at high quality, 1536×1024. The
  portrait came from the brief below and was then edited into the pub (same
  man, same pose) with: *"Change only the setting: he is behind the bar of a
  classic Australian corner pub on a bright afternoon. A long, worn timber bar
  top, a row of chrome beer taps with plain unbranded handles, back-bar shelves
  of bottles and pint glasses, pressed-metal ceiling, green-tiled bar front, big
  windows with daylight, pub tables softly out of focus."*

  Original brief:

  > Candid editorial portrait of Norm, an Australian hospitality veteran in his
  > early fifties: sun-weathered skin, short salt-and-pepper hair and beard, a
  > big warm open grin with crinkled eyes, genuinely pleased to see you,
  > looking straight at the camera. He leans on a light timber bar with his
  > forearms, a coffee cup beside him, wearing a deep forest-green work shirt
  > with sleeves rolled to the elbow (no apron), a white tea towel over one
  > shoulder. He is right of centre; the left third is the clean, empty end of
  > the bar and a softly blurred, bright dining room. Australian morning light
  > through big windows, white walls, plants, warm and fresh. Shot on a 50mm
  > lens at f/2.8, natural light, true-to-life colour and real skin texture,
  > slight asymmetry, gentle film grain, magazine-feature feel, not a stock
  > photo, nothing glossy or CGI-looking. No readable text, no logos.

- **New images of Norm:** pass the hero portrait to the image *edit* endpoint
  as a reference, so it is recognisably the same man, and keep the wardrobe
  above.
- **Composition:** leave the lower-left third calm wherever a product card
  will sit over the image.
