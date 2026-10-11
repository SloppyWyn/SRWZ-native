# Super Robot Taisen Z (PS2, 2008): route map and branch conditions

Compiled 2026-10-07 from web sources (no game data). For PLAYTHROUGH.md test planning.
Sources, cited by tag below:
- **[W]** SRW Wiki (JP), 全話一覧/Z and 隠し要素/Z: https://srw.wiki.cre.jp/wiki/全話一覧/Z , https://srw.wiki.cre.jp/wiki/隠し要素/Z
- **[A]** Akurasu wiki (EN), the main English source: flow chart with SR points, secrets and branch choices at https://akurasu.net/wiki/Super_Robot_Wars/Z/Flow_Chart (mirror neo-akurasu.duckdns.org), plus /Z/Timeline. The page has no stages after 43, so stages 44-60 come from [W], [LP], [G] and [N]. Akurasu's "ZEUTH Points" are the same counter that [W]/[N] call ZAFT points.
- **[LP]** Let's Play Archive, full playthrough of both protagonists, ZAFT route, all endings: https://lparchive.org/Super-Robot-Wars-Z
- **[G]** gamers-high.com late-route and ending page: https://gamers-high.com/srwz/ed-point.html
- **[N]** Namu wiki (KO, via JP mirror) strategy page: https://ja.namu.wiki/w/슈퍼로봇대전 Z/공략
- retro-trans/SRW-Z has no route docs; its tools address scenarios by record number (compare_translation.py --rec N). Mapping record numbers to stages is unverified and would be a useful tooling job.

Pages were read through a summarising fetcher, so exact menu wording in Japanese is quoted only where a source gave it. Verify menu text on screen before scripting pad input.

## Summary
- 60 stages per playthrough, 107 scenarios in total. Protagonist (Setsuko/Rand) picks stages 1-9, 21, 26-40, 47, 59, 60.
- Menus: after 15 Pacific/Galia (16-19, rejoin 20); after 32 Setsuko Gibraltar/Border Patrol, Rand Renton/Ship (33-35, rejoin 36); after 47 normal/hidden ZAFT route (48-53, 56-57; 54-55 common). First run: ZAFT needs ZAFT points + SR >= 36; NG+ always offers the choice.
- In-stage choices: S2 (subdue Gundams), Galia 17 (relay team), 58 (memory: -15 ending points), 59 (ending choice when points >= 18 or <= 6). 5 endings; bad ending has its own final stage.
- Cheats: no scenario/route/win codes exist publicly. Raw RAM on SLPS-25887 (assumed): money 0x558560 (32-bit), Blue Stones 0x5585A8, SR 0x55856F (8-bit), parts from 0x558700. Route flags probably live in the same save block; find them by diffing.
- Units: §8 lists join/leave per route and 15 conditional units a test run must deliberately unlock.
- Minimum coverage: one run per protagonist plus save-branches after 15, 32, 47 and before 58; ZAFT route cheapest via NG+.

## 1. Shape of the game
60 stage numbers, 107 distinct scenarios across all routes. Every playthrough is exactly 60 stages; branches swap which scenario plays at a number, they never add stages.
- Protagonist split 1-9, common 10-15, choice after 15 Pacific | Galia (16-19), common 20
- protagonist split 21, common 22-25, protagonist split 26-32
- after 32: Setsuko Gibraltar | Border Patrol; Rand Search for Renton | Look After the Ship (33-35)
- protagonist split 36-40, common 41-46, protagonist split 47
- after 47: normal | hidden ZAFT (48-53), common 54-55, normal | ZAFT (56-57)
- common 58 (in-stage memory choice), protagonist split 59 (ending choice), 60: Setsuko final | Rand final | bad-ending final
Rejoin points: 10, 20, 22, 36, 41, 54 and 58, 60 per protagonist.

## 2. Scenario list (S = Setsuko, R = Rand; JP titles from [W], EN from [A]/[LP], both fan titles)
| # | Setsuko | Rand | Notes |
|---|---|---|---|
| 1 | ザ・ライトスタッフ The Right Stuff | さすらいの修理屋 The Wandering Mechanic | |
| 2 | 怒れる瞳 Angry Eyes | 旅立ちの日 Day of Departure | S: in-map choice |
| 3 | 二つの世界 Two Worlds | 掟破りの助っ人達 Lawbreaking Helpers | |
| 4 | 異星人襲来 Alien Invasion | それぞれの旅、それぞれの事情 Respective Journeys | |
| 5 | 目覚めの日 Day of Awakening | ムーン・ライダーズ Moon Riders | |
| 6 | 超重神降臨 Descend, Super Gravity God | 祭りの夜に The Night of the Festival | |
| 7 | 月光、怒りに染めて Moonlight, Tainted by Rage | 月の女神 Goddess of the Moon | |
| 8 | 世界の終わる時 The End of the World | 天翅の記憶 Angel Memories | |
| 9 | 時空破壊 Dimension Crash | 世界の終わりの日 The Day the World Ends | |
| 10-15 | common: Break the World; Paradigm Shift; Blue Sky Fish; Stand Tall, Warrior of Space; The Gathering Strangers; To an Unseen Tomorrow | | choice after 15 |

16-19 (both protagonists): Pacific 16 交差する決意 Crossing Resolves, 17 青い放浪者 Blue Drifter, 18 血に染まる瞳 Bloodstained Eyes, 19 よみがえる翼 Resurrected Wings. Galia 16 特異点 Singularity Point, 17 地獄のエキデン The Relay from Hell (team choice), 18 さらば、我が友よ Farewell, My Friend, 19 建国のブリザード Nation-Founding Blizzard.
Series per route [W]: Pacific = God Sigma, Gundams except Turn A, Gravion, Eureka 7. Galia = Mazinger, Zambot 3, Orguss, Xabungle, Turn A, King Gainer. ([A] lists Gundam X, SEED Destiny, Zeta for Pacific; Grendizer for Galia.)

| # | Setsuko | Rand |
|---|---|---|
| 20 | common: A World Without Lies | |
| 21 | 残されるもの That Which Remains | 父の思い出 Memories of Father |
| 22-25 | common: My Own Exodus; Challenge from the Hyakki; Photon Power Lab Recapture; Darkness' Lair | |
| 26 | Hong Kong City | Into the Nature |
| 27 | Rising Fissure | The Ghost From 15 Years Ago |
| 28 | Silver Fang | Spirit Cosplayer |
| 29 | The Boiling Sea | Outsider |
| 30 | The Place of Sin | Acperience |
| 31 | Born From Our Bonds | Shattered Past |
| 32 | The Power Called Sadness | So I Can Be Myself |

33-35: S Gibraltar (Gundams): Pulse of Zeta; The False Queen and the Masked Princess; Shadow of the War God. S Border Patrol (supers): When the Stars Shine; To Whom Does This Planet Belong?; Rosary of Grief. R Search for Renton: Lonely Runaway; Strange Contact; Morning Glory. R Look After the Ship: O Distant Friend; The Angry Railroad King; Over Battle. Renton search party [W]: Katsura, Jiron, Chill, Garrod, Gain; everyone else stays with the ship.

| # | Setsuko | Rand |
|---|---|---|
| 36 | The Wailing Night Sky | Indicative Tomorrow ([LP] "Tomorrow Revealed") |
| 37 | Reshuffle in the New Earth Federation | Purging Storm |
| 38 | Dancing Nightmare | Preplanned Showdown |
| 39 | Raise the Flag | Start It Up |
| 40 | Impact Again | Prelude to Destruction |
| 41-46 | common: Cross Point; The Beginning of the End; Destiny and Freedom; The Falling Sun; Inheritors of the Legacies; Justice Amidst the Chaos | |
| 47 | Countdown | Our Path |
([W] JP table lost the Rand column for 36-40. [LP] and [A] both show 36-40 split by protagonist.)

48-57 (both protagonists share these):
| # | Normal route | Hidden ZAFT route |
|---|---|---|
| 48 | 楽園の追放者 Exiles From Paradise | 裏切りの月光 Traitorous Moonlight |
| 49 | Illusory Metropolis | Light of Despair, Glimmer of Hope |
| 50 | A Human's Heart, An Angel's Dream | I am D.O.M.E. |
| 51 | Separation | The Crimson Path |
| 52 | The Truth About the Black History | Gain Over |
| 53 | Lunar Showdown | Warped Judgement |
| 54 | common: Victory March of the Soul (ZAFT route has event changes) | |
| 55 | common: Shine Forevermore, Our Planet (same) | |
| 56 | Your Self, My Self | The Final Power |
| 57 | Climactic Light | Wish Upon a Star |
| 58 | common: Memories (in-stage choice) | |
59: Setsuko World in Black, Rand Overpainted Tomorrow. 60: Setsuko My Future, Everyone's Future; Rand My Future, Your Future; bad final 果てなき戦いの環へ (both protagonists). Total 107.

## 3. Branch points and what decides them
| Where | Kind | Decides | Source |
|---|---|---|---|
| New game | menu | Protagonist: stages 1-9, 21, 26-40, 47, 59, 60 | all |
| S2 | in-map victory choice | "Escape the colony" vs "Subdue the hijacked Gundams" (defeat Chaos, Abyss or Gaia). Subduing gets Shinn's Sword Impulse and Alex's Zaku. No stage-order change. | [A] |
| After 15 | menu ガリア部隊を希望する / 太平洋部隊を希望する | Galia vs Pacific 16-19; rejoin 20 | [LP], [W] |
| Galia 17 | menu | Sara team or Adette team for the relay; units only | [A] |
| After 32 | menu per protagonist | S: Gibraltar vs Border Patrol. R: Search for Renton (レントンを探しに行く) vs Look After the Ship. Rejoin 36. | [W], [LP], [N] |
| After 47 | automatic or menu | Normal vs hidden ZAFT for 48-53, 56-57 | sources disagree |
| 58 | in-stage menu | "Throw the memory away and live in the City" (メモリーを捨て、シティで生きていく) = ending points -15, forces a low ending | [W], [N], [G] |
| 59 | in-stage menu, only at high or low ending points | Ending (§4) | [W], [N], [LP] |

### Hidden ZAFT route (after 47)
Driven by ZAFT points ([A]/[G]: ZEUTH or flag points) and SR points at the end of 47.
- NG+: menu always appears after 47. Easiest way to test ZAFT: clear once, use the clear save.
- First playthrough: ZAFT >= 10 and SR >= 36 gives the menu ([W], [G]); [N] says Rand needs only >= 8. Auto ZAFT: [W] (ZAFT >= 9 and SR < 36) or (7-9 and SR >= 36); [N] (>= 9) or (SR >= 36 and >= 7); [G] only 7-9 with SR >= 36. Otherwise normal. [W]: Rand's first run maxes at 9 points, so Rand needs SR >= 36 and gets no menu; [N]: on Rand the +9 path needs the Pacific route.
- Points from [G]: Pacific 16; Rand 29, 30, 36, 38; Setsuko 2, 8, 29, 35, 36, 38; common 43. [A] stage 38: Shinn's Impulse fighting or defeating Kira's Freedom +2/+1, Freedom killing Stella's Destroy -1.
- Menu text after 47 per [W]: 「ザフトへ戻る」 vs 「ZEUTHとして戦う」; confirm on screen which is which.
Disagreement: [A]'s timeline labels ZAFT "(Default)"; [W], [N], [G], [LP] treat ZAFT as hidden. Go with ZAFT = hidden.

## 4. Endings
Ending points earned through 58 (max +21 per [N]): common 41-46, 47, 55 (defeating Shagia, Olba, Gym Ghingnham), 58 choice (-15), per-stage conditions on 48-57 ([G]); [A] lists 41 (Emaan units), 42 ("Saving Aki" +1).
| Ending points | 59 choice | Result | Final |
|---|---|---|---|
| >= 18 | 全てが元通りになるのを望む | "Restored" | own 60 |
| >= 18 | 世界の安定を望む | "Fully stable" | own 60 |
| 7-17 | none | Normal "stable" | own 60 |
| <= 6 | 世界の安定を望む | "Unstable"/sad | own 60 per [LP]; [G] says special final |
| <= 6 | 自分では決められない | Black History bad ending | 果てなき戦いの環へ |
[N], [G], [LP]: <= 6 and 7-17; [W]: <= 7 and 8-17. Use 6, treat 7 as uncertain.

## 5. Other conditions inside stages (not stage order)
- S3: not defeating Emma's Mk-II changes Amuro's later unit [A].
- 42 human bomb (saving Aki): Setsuko 7+ Aki points, Rand 4+ and SR >= 32.
- Stella joins: 8 points, or 7 + SR >= 28. [W] normal 56 / ZAFT 51; [A] ZEUTH 51 / ZAFT 56 (disagree).
- Four joins 38: 5 points, or 4 + SR >= 28.
- Athena 41: Kei must persuade before Olson. Rubina: let her escape at 44 (also an ending point). Stage 44 has a special game over [LP].
- ZAFT route: Haman joins after 51; Rei and Talia rejoin with conditions at 56 [W].
- SR points raise difficulty; a weak bot lands on low SR, so the hidden route won't trigger by accident on a first run.

## 6. Test-plan notes for the port
- With save-state branching: one Setsuko run and one Rand run, Pacific on one and Galia on the other. Branch saves at end of 15 (other 16-19 route, stop at 20), end of 32 (other 33-35 sub-route, stop at 36), end of 47 (ZAFT needs NG+ or point conditions), and before 58/59 for ending variants.
- Endings: from a pre-58 save, choose -15 at 58 for the two low endings; the >= 18 pair needs a high-point run.
- Cheapest ZAFT coverage: finish one playthrough, NG+ from the clear save, pick at 47.
- Menu choices after 15, 32, 47 (intermission) and inside stages 2 (S), 17 (Galia), 58, 59; one pad macro per choice in config/game.json.
- Every protagonist-specific stage (1-9, 21, 26-40, 47, 59, 60) can hide pointer-table-only functions (Wall 16 lesson); rerun codeptrs.py on each new route.

## 7. Cheat codes and known memory addresses
Searched 2026-10-07. No public code list for scenario select, stage number, route flags, ZAFT or ending points, instant win, or invincibility. No PCSX2 pnach (PCSX2 wiki lists serials SLPS-25887 and SLPS-73270, no patches). Sources: 5ch thread https://kako.5ch.io/test/read.cgi/gameurawaza/1220524875 ; https://detail.chiebukuro.yahoo.co.jp/qa/question_detail/q1022181305 ; https://detail.chiebukuro.yahoo.co.jp/qa/question_detail/q11149167551 ; chaos-file.jp page https://chaos-file.jp/?z=t2&tid=658 not fetchable (expired certificate).
Version: no post names it; the thread is from September 2008, before The Best (SLPS-73270), so assume SLPS-25887 (original). Check against our ELF before use.

Raw PS2 RAM codes (type 0 = 8-bit, 1 = 16-bit, 2 = 32-bit; pnach: patch=1,EE,<code>,extended,<value>):
| Effect | Code | Address / width | Status |
|---|---|---|---|
| Money 99,999,999 | 20558560 05F5E0FF | 0x00558560, 32-bit | confirmed by several posters |
| Blue Stones 999,999 | 205585A8 000F423F | 0x005585A8, 32-bit | not confirmed |
| SR points 99 | 0055856F 00000063 | 0x0055856F, 8-bit | not confirmed |
| Parts x99 (range) | 00558702 63 stepping +4 up to 00558806 63 | 0x00558702..0x00558806, 8-bit | one report of a freeze when saving after use |
| Parts all 90 (serial write) | 40558700 00240001 / 005A005A 00000000 | 0x00558700, 36 entries | one poster |
| Garrod 9999 (kills or a pilot stat) | 10529D94 270F | 0x00529D94, 16-bit | unlabelled |
| Gain 9999 | 1052C624 270F | 0x0052C624, 16-bit | same |
| Gorilla 9999 | 1052F6F4 270F | 0x0052F6F4, 16-bit | same |
The pilot entries are 0x2890-0x30D0 apart, suggesting per-pilot records of that size (unchecked).

Encrypted Action Replay 2 codes (need decrypting): master (format 3) EC878530 1456E79B, (format 4) EC878530 1443DFC8. Money max 1CC40688 17E9C70C. Blue Stones 1CC406D0 144F25CC. Money not spent on upgrades 1C84C6E8 1456E7A5. PP to max on any change: 1C8269A4 1456E7A5 / 1C8D6E90 1456E7A5 / 1CB2F954 1456E7A5. Funds earned xN: 1CB4BA90 0436E7A7 plus an incomplete second line. A text list also claims EN/ammo infinite, unlimited moves, kill counts and upgrades, without codes.

Suggestions: money, SR and Blue Stones sit in one block around 0x558560-0x558810, likely the in-RAM save copy; stage number, protagonist, route flags, ZAFT and ending points are probably there too. Find them by dumping this block at two intermission saves one stage apart (PINE or snapshots) and diffing. Put them in config/game.json as named addresses with a dev env var that writes them: scenario select and route forcing without a cheat device. For instant wins, find the map victory-check flag ourselves.

## 8. Unit and pilot join / leave points
Source: Akurasu acquisition timeline https://akurasu.net/wiki/Super_Robot_Wars/Z/Timeline via a summarising fetcher. Column alignment is shaky (common stages 10-15, 20, 22-25, 41-46 landed under "Setsuko"; several 48-56 cells look swapped vs [W]). Treat as a checklist of who and roughly when; confirm exact stages against the in-game unit list. Full weapon lists: Akurasu "Playable Unit List" and "Playable Unit Database" (linked from https://akurasu.net/wiki/Super_Robot_Wars/Z), not copied.
Notation: + joins, - leaves, x lost for good, NPC = guest, (B) = Bazaar purchase.

### 1-15
- Setsuko 1-9: +Denzel, Toby, Setsuko (Virgola 1-3) 1 · +Shinn (Sword Impulse; needs "subdue" at 2), Alex/Athrun (Zaku), Rey, Lunamaria, Minerva 2 · +Kamille (Mk-II), Quattro, Apolly, Roberto (Rick Dias), Argama 3 · +Duke (Grendizer), Kouji/Maria/Hikaru (Spazers) 4 · +Kappei (Zambo Ace) 5 · +Touga (Gran Kaiser), God Gravion crew 6 · +Toshiya (God Sigma), Zambot 3 + King Beal, Emma, Reccoa (Nemo) 7 · all but Glory Star leave after 8 · Denzel x 9 (PP to Setsuko); +Kei, Olson (Bronco II).
- Rand 1-9: +Rand & Mel (Gunleon), Garrod (Gundam X Divider), Gain (Gatchiko) 1 · +Gainer (King Gainer), Sara, Gauli, Bello 2 · +Jiron, Chill (Walker Gallier), Rag (Xabungle), Blume, Dike, Iron Gear crew 3 · +Eureka (Nirvash), Holland, Matthieu, Stoner, Hilda, Gekko 5 · +Loran (Turn A) 6 · +Sochie, Miashei 7 · +Aquarion crew (Apollo, Sirius, Sylvia, Reika, Pierre, Glen), Gavane, Joseph, Harry (SUMO) 8 · all leave after 9.
- Common 10-15: big guest rosters; Big O (Roger) leaves after 11; Mel leaves 11, returns 15 (Rand); +Asakim (Shurouga, guest) 15 on Rand; Kei (Bronco II) x 15, +Orguss (Kei) and Glomar crew.

### 16-19
- Pacific: Gekko State, Freeden 16; Grendizer, Marine Spazer, Baldios (event) 17; Amuro (red Rick Dias or black Mk-II per S3 secret), Katz 18; Shinn's SEED 18; Kira (Freedom), Waltfeld, Archangel, Athrun (Savior) 19.
- Galia: Big O (Roger), Adette (red Dorbeck) 17; Tsugumi, Jun (Aquarion Luna) 18; Roger leaves 19; Baldios, Amuro, Katz, Athrun, Gekko State join 19 so routes converge by 20. Gain (Gatchiko) leaves 20.

### 21-32
- Setsuko: +Lowen (Chaos Leo) 21; Toby x 21, Setsuko to Virgola Kai · Gatchiko x, +Gain (Emperanza), Getter Dragon (Ryoma, Hayato, Benkei) 22 · Lady Command (Michiru), Big O 23 · Mazinger Z, Great Mazinger (Tetsuya), Boss Borot, Diana A, Venus A 24 · Big O leaves 25 · Dijeh 26 (secret) · God Gravion re-forms, Gran Kaiser x 28 · Argama, Minerva 30 · Lowen 31 · Virgola Kai x 32, Virgola VWFS / Glory.
- Rand: Lowen, Asakim 21 · Tekkouki 25 (Rand only, or after 40) · Gundam DX (Garrod), Jamil in Gundam X 27 · Ziene (Eliphas) 29 · Kira, Waltfeld, Enil, Archangel; Renton becomes Nirvash pilot 30 · Lowen, Asakim 31 · Gunleon powerup 32.

### 33-35
- Gibraltar (S): Fa (Methuss), Henken (Radish), Zeta (Kamille), ReGZ (Amuro) 33; Reccoa leaves 34.
- Border Patrol (S): Zambot/Grendizer/Mazinger combination attacks; Grendizer, Marine Spazer back 34; Zeta 35.
- Search for Renton (R): Paula (G-Falcon), DX + G-Falcon, Enil, Spearheads (guest) 33; Olson & Athena (Nikick), Big O 34; Eureka back as sub-pilot 35.
- Look After the Ship (R): Flick (Jolar), Baldios powerup 33; Big O, G-Falcon, Enil 34; Grendizer, Marine Spazer 35.

### 36-47
- S 38: Banjo (Daitarn 3), Four (Gaplant, secret). R 38: Kira, Archangel leave. R 39: Olson (Orguss II); Nirvash spec. 2.
- 41 Athena (secret order). 42 Fei (Grantrooper); Touga, Eina leave. 43 Destiny (Shinn), Legend (Rey); Athrun, Meyrin leave; 2nd half Strike Freedom, Eternal (Lacus), red Gaia (Waltfeld), Turn X (Gym), ν Gundam (Amuro); Freedom x. 44 Rubina (NPC), Sol Gravion, Fixer-1 (David); Baldirosier. 45 Jerid & Mouar (NPC), Doran (Birin, Maria Maria); Xabungle 2 (B). 46 Infinite Justice (Athrun; Savior x), Strike Freedom, Archangel, Akatsuki (Neo). 47 Lowen x both.

### 48-57 (alignment unreliable)
One-route joins per Akurasu: Beck (Beck The Great), Big O Final Stage; Jinba, Kejinan, Enge, Jaboli; Haman (Qubeley); Tifa; Lancerow (Crowda); Cynthia (Dominator); Sandman (God Sigma Gravion); Yzak (GOUF Ignited); Sarah Zabiarov; Reccoa back (Pallas Athena, stats reset); Rey, Talia back (ZAFT only per [W]); Shinn, Lunamaria rejoin 56 on ZAFT side. 57: Aquarion Mars; Sirius, Rena sub-pilots, Solar Sword.
### 60
Nirvash spec. 3, Anemone (Nirvash THE END), Federation aerial battleship (Jurgens, Dominic), Golem (Artham), white Dorbeck (Kejinan, Enge). Some ships unavailable per half.

### Conditional / route-exclusive units
| Unit | Condition | Source |
|---|---|---|
| Shinn + Alex early | S2 "subdue the Gundams" | [A] |
| Amuro's unit | S3: Emma's Mk-II not defeated gives black Mk-II, else red Rick Dias | [A] |
| Rushrod (Yassaba) | Gainer, Gain, Sara, Gauli, Bello, Adette kills >= 100 by a set stage | [A] |
| Dijeh | Avg level >= 25 across nine pilots by 25; Amuro and Katz avg kills >= 25 | [A] |
| Four | 5 points, or 4 + SR >= 28; joins 38 | [A], [W] |
| Stella | 8 points, or 7 + SR >= 28; 51 or 56 by route | [A], [W] |
| Athena early | 41: Kei persuades before Olson | [A] |
| Aki saved | 42: S 7 points, R 4 + SR >= 32 | [A], [W] |
| Rubina | 44: let her escape | [W] |
| Tifa | 4 points, or 2 + SR >= 40 | [W] |
| Sarah Zabiarov, Reccoa back | 9-part secret, 34-56 | [A] |
| Spearheads | 35 (Renton) persuasion + Normal-or-better ending | [A] |
| Haman | ZAFT route, after 51 | [W] |
| Rey, Talia | ZAFT route, conditions at 56 | [W] |
| Tekkouki | Rand only (or after 40) | [A] |
| Glory Star / Gunleon | protagonist-locked | all |
| Bazaar units (TFO, Hero's Mark, Shadow Angel Feather, Mega Bazooka, Great Booster, G-Bit, Xabungle 2, ...) | Blue Stones; some one route only | [A] |
Coverage: both protagonist rosters, both 16-19 routes, all four 33-35 sub-routes and both 48-57 routes are needed to see every unit; secrets need deliberate play or pokes (§7).
