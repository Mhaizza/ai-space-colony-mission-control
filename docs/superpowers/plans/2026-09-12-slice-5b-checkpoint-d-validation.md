# Checkpoint D — Implementation และ validation

## ขอบเขตและสถานะ

วันที่ 2026-09-12 บอสอนุมัติ [แผน Checkpoint D](2026-09-12-slice-5b-checkpoint-d-change-decision-ux.md) พร้อม implementation/tests รวม test กรณี acknowledged decision ID ไม่ตรงกับ GET โดยยังห้าม commit, push หรือเปิด PR

- Worktree: `C:\Users\Mhaiz\Projects\ai-space-colony-mission-control-checkpoint-d`
- Branch: `codex/slice-5b-checkpoint-d-implementation`
- Baseline HEAD: `5492afaf9655a8634dd4c69e30d8839f7fa4519b` รวม PR #29
- งานยังเป็น tracked modifications และ untracked files ผู้ตรวจต้องอ่านทั้งสองส่วน
- ไม่มี backend/API/generated client/dependency/migration/legacy Board/auth provider changes และไม่มี create approval หรือ supersede request

## พฤติกรรมที่ implement

- Pending request ที่มี can_decide และ current decision เสนอ Change decision; request ที่ไม่มี current decision ใช้ initial Approve/Reject เดิม
- Dialog แสดง previous decision/ID/reason แบบอ่านอย่างเดียว เริ่มตัวเลือกใหม่ด้วยค่าเดิมและเหตุผลใหม่ว่าง ผู้ใช้เลือก approve/reject ได้ เหตุผลว่างเปลี่ยนเป็น null ข้อความอื่นคงเดิม
- จับ prior ID ตอนเปิด dialog และตรวจอีกครั้งก่อน confirm ถ้า current ID เปลี่ยนจะบล็อกและแจ้งให้ปิด dialog โหลดรายละเอียดใหม่ แล้วเปิดอีกครั้ง ไม่แทน ID ใน draft เดิม
- Controller เดิมใช้ discriminated union แยก submit/supersede ส่งผ่าน generated APIs และ mutation hooks ด้วย explicit credentials; ไม่มี automatic retry
- Request เดียวมี synchronous lock ร่วมทั้งสอง modes; uncertain retry คง route/prior ID/body/key แม้ backend snapshot กลายเป็น terminal แล้ว
- Recorded operation เก็บ acknowledgedDecisionId และเปิด intent ใหม่ได้เมื่อ exact detail/list reconciliation สำเร็จและ fresh/pending/eligible พร้อม current ID ตรงกับ acknowledged ID
- หาก current ID ขัดกับ acknowledged ID จะคง recorded และแสดงคำแนะนำ Reload the page พร้อมบล็อก intent ใหม่ การ Refresh ภายหลังไม่ล้าง mismatch flag; page reload เริ่ม controller ใหม่แล้วต้องอ่าน snapshot ใหม่
- Failed post-write reads ใช้ GET-only recovery เก็บข้อมูลเดิม 4xx rejection ต้อง reconcile ก่อนเปิด intent ใหม่ และคงเหตุผลใน dialog ไว้แก้
- ใช้ freshness constant ร่วม UI/controller และรักษา page/session teardown รวมการล้างเฉพาะ approval cache

## Tests และหลักฐาน

ก่อนเปลี่ยน controller เพิ่ม tests แล้วพบ RED 3 กรณี: supersede ไม่ส่ง, sequential intent ไม่ปลดล็อก และ ID conflict ไม่มีข้อความ Reload จากนั้นแก้จน GREEN ก่อนเชื่อม UI

ก่อนเปลี่ยน dialog/detail เพิ่ม tests แล้วพบ RED สำหรับ previous-decision UI และ Change decision action จากนั้น implement จน GREEN ปรับ negative assertion ของ C เฉพาะจุดที่ D ตั้งใจเพิ่ม Change decision หลัง initial success

Tests เพิ่มเติมครอบคลุม supersede shared lock, frozen retry หลัง terminal snapshot, session change ระหว่างรอ token, การ reset detail/list cache โดยเก็บ unrelated cache, detail/list read failure แยกกัน, terminal states, acknowledged-ID conflict ทั้ง submit/supersede, sticky mismatch หลัง Refresh, validation reason retention ทั้งสอง modes และ Back/close/reopen/Mission switch ของ uncertain supersede

| การตรวจ | ผล |
| --- | --- |
| Targeted controller + UI integration ล่าสุด | PASS: 48 tests (33 controller + 15 integration) |
| Frontend เต็ม `npm run test -- --maxWorkers=2` | PASS: 213 tests / 34 files รวม B/C/Board/hydration regression |
| Coverage gate | PASS: 100% เฉพาะ ActivityFeed/backoff ตาม config เดิม ไม่ใช่ coverage ทั้งแอป |
| `npm run lint` | PASS; ESLint ของ test files ที่แก้ภายหลังผ่านด้วย |
| `npx tsc -p tsconfig.json --noEmit` | PASS |
| `npm run build` | PASS ใน local-auth mode หลังหยุด dev server |
| Cypress dev, Electron | PASS: 4 scenarios, mocked API ทั้งหมด |
| Cypress production build, Electron | PASS: 4 scenarios, mocked API ทั้งหมด; ปิด server ทดสอบแล้ว |
| Markdown links / lint | PASS: links 39 files, lint 46 files ไม่มี error |
| Git scope / whitespace | PASS: baseline HEAD เดิม, staged diff ว่าง, ไม่มี whitespace error |

การรันชุดเต็มพร้อม production build หนึ่งรอบล้มเหลวด้วย timeout 5 วินาทีในหลายไฟล์ รวม LocalAuthLogin/CustomFieldForm ที่ไม่ได้แก้ หลัง build จบ รันใหม่จำกัด workers เป็น 2 ผ่านทั้งหมดโดยไม่เปลี่ยน timeout หรือแก้ assertions เพื่อหลบ failure รอบแรกก่อนเพิ่ม tests สุดท้ายเคยผ่าน 211 tests ด้วย default workers แล้ว

Browser scenarios: desktop/mobile เปิด initial approve, cancel change dialog ด้วย Escape, supersede ค่า approve พร้อมเหตุผลใหม่, supersede อีกครั้งใช้ successor ID และ key ใหม่ แล้ว rejected terminal ต้องไม่มี Change decision รวม regression initial approve/reject ของ C ไฟล์ spec เดิมยังได้รับ strict hydration guard จาก C

ภาพ dialog desktop/mobile ถูกตรวจด้วยสายตาและย้ายออกจาก repository ไปเก็บที่ `C:\Users\Mhaiz\.codex\visualizations\2026\09\11\01a08f45-ee3a-7ad1-9a40-48629da434f3\checkpoint-d` ชื่อ `checkpoint-d-change-1440.png` และ `checkpoint-d-change-390.png` ไม่ใช่ข้อมูลจริง ไม่มี real approval mutation ระหว่างทดสอบ

Backend ไม่ได้เปลี่ยนและไม่ได้รัน backend suite ซ้ำในงาน D นี้ ไม่อ้างว่า `make check` หรือ GitHub CI/CodeQL ผ่านสำหรับ uncommitted implementation นี้

## ส่งต่อ review

บอสส่งรายงาน independent implementation review ของ Claude วันที่ 2026-09-12: PASS ไม่มี blocking findings ผู้รีวิวอ่านโค้ด/diff แต่ไม่ได้รัน tests เอง โดยมีข้อสังเกตไม่ block ว่าขาด test เฉพาะ 4xx rejection ตามด้วย reconcile GET ล้มเหลว ผลรีวิวนี้เป็นรายงานที่ผู้ใช้ส่งมา ไม่ใช่การรัน independent review ซ้ำโดย Codex

หลังบอสให้ดำเนินการต่อ เพิ่ม controller regression tests 4 กรณี ครอบคลุม submit/supersede แยก detail/list GET ล้มเหลวหลัง HTTP 422 ยืนยัน phase rejected, refreshFailed, การเก็บ reason, บล็อก confirm/retry ไม่ให้ส่ง POST เพิ่ม, Refresh ที่ยังล้มเหลวต้องคงการบล็อก และปลดล็อกเมื่อ Refresh สำเร็จโดยส่งเฉพาะ GET 2 ครั้ง ไม่แก้ production code

ต้องมี independent implementation review และ human acceptance ก่อนขอ commit/push/Draft PR authorization แยกต่างหาก ไม่มีการเปิด PR หรือเริ่มงานนอก Checkpoint D

### Validation หลังเพิ่ม regression tests (2026-09-12)

- `npx vitest run src/components/mission/governance/useMissionDecision.test.tsx --maxWorkers=2`: PASS 37 controller tests
- `npm run test -- --maxWorkers=2`: PASS 217 tests / 34 files รวม 15 integration tests; coverage gate PASS 100% เฉพาะ ActivityFeed/backoff ตาม config เดิม
- `npx eslint src/components/mission/governance/useMissionDecision.test.tsx`: PASS
- `npx tsc -p tsconfig.json --noEmit`: PASS
- `npx prettier --check src/components/mission/governance/useMissionDecision.test.tsx`: PASS หลังจัด formatting
- `git diff --check`: PASS ไม่มี whitespace errors; มีคำเตือน LF/CRLF จาก Git config
- รอบ follow-up นี้แก้เฉพาะ controller test และเอกสาร validation ไม่รัน build/Cypress/backend ซ้ำ ผลส่วนเหล่านั้นในตารางก่อนหน้าเป็นหลักฐานจากรอบ implementation เดิม ไม่มีการ commit/push/เปิด PR
