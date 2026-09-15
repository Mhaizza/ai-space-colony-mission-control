# Slice 5B — Checkpoint D: แผน Change decision UX

## สถานะและ baseline

อัปเดต 2026-09-12: Claude รายงานผล plan review เป็น PASS ไม่มี blocking findings และบอสอนุมัติแผนพร้อม implementation/tests รวมกรณี acknowledged ID ไม่ตรงกับ GET แล้ว งานดำเนินการบน `codex/slice-5b-checkpoint-d-implementation` โดยยังห้าม commit, push หรือเปิด PR ข้อความเกี่ยวกับการรออนุมัติด้านล่างเป็นประวัติรอบวางแผน ไม่ใช่สถานะปัจจุบัน

- Baseline: `main @ 5492afaf9655a8634dd4c69e30d8839f7fa4519b` รวม PR #29 แล้ว
- Worktree: `C:\Users\Mhaiz\Projects\ai-space-colony-mission-control-checkpoint-d`
- Branch: `codex/slice-5b-checkpoint-d-plan`
- เป็น Git worktree แยกไฟล์ทำงาน แต่ใช้ Git metadata ร่วมกับ clone Checkpoint C ไม่ใช่ independent clone
- CodeQL หลัง merge ของ baseline ผ่านแล้ว: run `34659595317`
- เอกสารหลัก: [Mission Operations UX](../specs/2026-08-18-slice-5b-mission-operations-ux-design.md)
- งานก่อนหน้า: [Checkpoint C plan](2026-09-11-slice-5b-checkpoint-c-human-decision-ux.md) และ [validation](2026-09-11-slice-5b-checkpoint-c-validation.md)

## เป้าหมายและขอบเขต

ผู้ใช้เปลี่ยน decision ที่ยังมีผลของตนเองได้ เมื่อ approval request ยัง pending และ backend ระบุ `can_decide: true` ใช้ `POST /api/v1/mission/approvals/{request_id}/supersede` ที่มีอยู่แล้ว ไม่แก้ backend, API contract, schema, migration, generated client, dependencies, ADR-23 หรือ legacy Board UI ไม่เพิ่ม create approval หรือ supersede request

การเปลี่ยน decision หมายถึงสร้าง decision ใหม่อ้างถึง decision เก่า ไม่เขียนทับประวัติเดิม ไม่เปิดทางแก้ request ที่ approved/rejected/expired/superseded แล้ว การกด Reject อาจทำให้ request terminal จึงไม่รับประกันว่าผู้ใช้จะเปลี่ยนกลับได้

## หลักฐานจากโค้ดปัจจุบัน

| ตำแหน่ง | สิ่งที่ยืนยันแล้ว / ผลต่อแผน |
| --- | --- |
| `backend/app/api/mission_approvals.py:247` | Supersede route ใช้ `ApprovalDecisionResponse` เช่นเดียวกับ submit decision |
| `backend/app/mission/approval_service.py:657` | ตรวจ human actor, pending, request ownership ของ prior decision, caller ownership และ effective decision ก่อนสร้าง successor |
| `frontend/src/api/generated/model/supersedeDecisionRequest.ts` | Payload มี `supersedes_decision_id`, `decision`, `reason?` |
| `frontend/src/api/generated/mission-approvals/mission-approvals.ts:983` | มี generated supersede hook พร้อมใช้ ไม่ต้อง api-gen |
| `useMissionDecision.ts` | Controller อยู่ระดับหน้า มี auth/session isolation, frozen intent, explicit credentials, manual retry และ exact-target reconciliation แต่ C บล็อกทุก operation ที่ไม่ใช่ rejected |
| `ApprovalDetailPane.tsx` | ตอนนี้เสนอ initial action เฉพาะ current decision เป็น null และใช้ freshness 15 วินาที |
| `DecisionDialog.tsx` | รองรับ initial approve/reject, optional reason, focus/cancel และ validation reason retention |

Backend ตรวจ idempotency replay ก่อน pending/prior-effective checks ดังนั้น retry intent ที่ยัง uncertain ต้องส่ง route/payload/key เดิม แม้ snapshot ล่าสุดเปลี่ยนสถานะแล้ว ห้ามใช้เกณฑ์ eligibility ของ intent ใหม่ไปบล็อก retry เดิม

## UX ที่เสนอ

1. Pending + can_decide + ไม่มี current decision: คง Approve/Reject ของ C
2. Pending + can_decide + มี caller-owned current decision: แสดง `Change decision` แทน initial actions
3. เปิด dialog แสดง Mission/action/request, decision เดิมและ ID, ตัวเลือก Approve/Reject, เหตุผลเดิมแบบอ่านอย่างเดียว และช่องเหตุผลใหม่ที่ไม่บังคับ
4. เริ่มตัวเลือกด้วย decision ปัจจุบันและเหตุผลใหม่ว่าง ไม่เดาว่าผู้ใช้ต้องการกลับทิศ ผู้ใช้เปลี่ยนเป็นอีกค่า หรือยืนยันค่าเดิมพร้อมเหตุผลใหม่ได้ตาม contract; ไม่มีการข้าม POST ว่าเป็น no-op โดย frontend
5. เหตุผลใหม่ `""` แปลงเป็น null; ข้อความอื่นคงเดิมไม่ trim เช่นเดียวกับ C
6. Confirm เท่านั้นจึงสร้าง key และ POST; Cancel/Escape ก่อนส่งไม่มี POST และไม่มี key
7. หลังสำเร็จอ่าน detail/list จาก backend แสดง successor current decision พร้อม lifecycle เดิม ไม่คำนวณ quorum/status/effect เอง

Terminal, read error, stale/fetching snapshot, identity mismatch หรือไม่มีสิทธิ์: ไม่มี action ที่ใช้งานได้ แสดงข้อมูลเดิมและช่องทาง Refresh ตาม C หากสิทธิ์หรือ current decision ID เปลี่ยนขณะ dialog เปิด ต้องบล็อก confirm แจ้งให้โหลดใหม่ และให้ผู้ใช้เปิด dialog ใหม่เพื่อเห็นเป้าหมายใหม่ ห้ามแทน prior ID ใน draft เดิมอย่างเงียบ ๆ

## Controller และอายุ intent

ใช้ controller เดิม ขยายด้วย discriminated union เพื่อแยก `submit` กับ `supersede` อย่างชัดเจน แต่ใช้ session/error/refetch machinery ร่วมกัน ไม่สร้าง controller ที่อาจส่งพร้อมกันสองตัวบน request เดียว

- Frozen intent ประกอบด้วย mode, request/Mission identity, proposed decision, reason, idempotency key และ `supersedes_decision_id` สำหรับ supersede
- เมื่อเปิด dialog จับ prior ID จาก `current_principal_decision.decision_id` ของ snapshot ที่ตรงเป้าหมาย
- เมื่อ confirm ตรวจ fresh snapshot ใหม่จาก QueryClient ว่ายัง pending/can_decide และ prior ID ตรง draft เดิม รวม auth session และ freshness แบบ C; ไม่จำเป็นต้องบังคับ GET ทุกครั้งที่คลิก
- ส่ง supersede ผ่าน generated hook/function ด้วย explicit Authorization เช่นเดียวกับ submit ใช้ `retry: false` และ captured caller context ตรวจซ้ำก่อน/หลัง await token
- Double-click guard ต้องเขียน ref แบบ synchronous ก่อน await และใช้ร่วมทั้งสอง modes
- `sending`, `uncertain`, `refreshing` ของ request เดียวกันบล็อก intent ใหม่ทุก mode; Back/ปิด drawer ไม่ล้าง attempt ที่ยัง uncertain
- `recorded` จะเริ่ม intent ถัดไปได้ต่อเมื่อ reconcile detail และ list สำเร็จ, snapshot ยัง fresh/pending/eligible และ current decision ID ตรง `decision_id` จาก response ที่ acknowledge ล่าสุด ต้องเก็บ acknowledged ID ใน operation เพื่อเทียบ ไม่ใช้เพียง `current_principal_decision !== null`
- หาก POST acknowledged แต่ refresh ล้มเหลว คง recorded banner และ GET-only refresh; ห้ามเปิด intent ใหม่จนอ่านยืนยันได้ครบ หาก GET คืน current ID ที่ขัดกับ acknowledged ID ให้บล็อกและแจ้งว่าข้อมูลเปลี่ยน ต้องโหลดหน้าใหม่เพื่อเริ่มจาก snapshot ใหม่ ไม่สลับเป้าหมายใน attempt เดิม
- เมื่อมี intent ใหม่ที่ยืนยันแล้วจึงแทน operation เก่าของ request นั้นใน page state; ประวัติถาวรยังมาจาก backend
- หลัง terminal ไม่เสนอ Change decision แม้ operation recorded แล้ว; หลัง 4xx rejection ให้ reconcile ก่อนเสนอ intent ใหม่ และหาก prior ID เปลี่ยนต้องเปิด dialog ใหม่
- หลัง session change/page unmount ล้าง operation และยกเลิก late reconciliation ตาม C; credential ไม่อยู่ใน query keys/mutation variables

รวมค่าคงที่ freshness ที่ UI/controller ใช้เป็นค่าเดียวเฉพาะจุดที่จำเป็น เพื่อป้องกัน eligibility ไม่ตรงกัน ไม่ refactor architecture ส่วนอื่น

## Error policy

| ผล | พฤติกรรม |
| --- | --- |
| HTTP 200 valid response | เก็บ acknowledged decision ID แล้ว GET detail/list exact target |
| 200 แล้ว GET ล้มเหลว | Recorded + read error; retry เฉพาะ GET ไม่ส่ง supersede ซ้ำ |
| 401/403 | แจ้ง sign-in/permission เปลี่ยน ไม่ retry อัตโนมัติ |
| 404 / prior decision หาย | แจ้งข้อมูลไม่พร้อมให้เปลี่ยนและ refresh |
| 409 request terminal / invalid_supersede / key conflict | แจ้ง conflict อย่างปลอดภัย อ่านใหม่ ไม่หมุน key เพื่อเลี่ยง conflict |
| 400/422 | เก็บ proposed decision และเหตุผลไว้แก้ ต้อง confirm ใหม่ด้วย key ใหม่ |
| Network, 408/429, 5xx, malformed success | Uncertain; manual retry mode/route/prior ID/payload/key เดิม |

ตรวจ error code กับ `approval_errors.py` ตอน implement ใช้ safe-message mapping ไม่ render server error object ดิบ Response validator ใช้ shape ของ `ApprovalDecisionResponse` และตรวจ echo request/decision/reason เช่น C

## งาน implementation และ tests หลังอนุมัติ

1. **Eligibility/dialog — RED แล้ว GREEN:** แสดง current/new decision ชัดเจน; terminal/capability/missing current/stale/mismatched identity บล็อก action; Cancel ไม่มี mutation; focus/Escape; reason retention
2. **Typed supersede intent — RED แล้ว GREEN:** route และ prior ID ถูกต้อง; key สร้างเฉพาะ confirm; double-click/shared request lock; retry แม้ global retries เปิดยังส่งเพียงครั้งเดียวจนกดเอง; retry mode/key/body เดิม
3. **Sequential intents — RED แล้ว GREEN:** initial submit → recorded → confirmed reads → change decision ได้ในหน้าเดิม; supersede → successor → supersede อีกครั้งใช้ successor ID และ key ใหม่; refresh failure/response mismatch ไม่ปลดล็อก
4. **Race/session — RED แล้ว GREEN:** prior ID, capability และ terminal เปลี่ยนระหว่าง dialog; Back/close/reopen; สลับ Mission; late response; Clerk/local session change; ตรวจ cache reset ว่าข้อมูล caller เก่าถูกล้างโดยตรง
5. **Reconciliation/error — RED แล้ว GREEN:** detail/list ล้มเหลวแยกกัน; cached empty list; known conflict; malformed body; ไม่ซ่อน acknowledged write และไม่ POST จากปุ่ม Refresh
6. **Regression/browser:** รักษา C/B/Board และ hydration tests; ปรับเฉพาะ negative supersede assertions ที่ D เปลี่ยนอย่างตั้งใจ คงข้อห้าม create approval ใช้ mocked browser desktop/mobile ทดสอบ submit→change, cancel และ terminal read-only

ไฟล์ที่คาดว่าจะเปลี่ยน: `useMissionDecision.ts` และ tests, `ApprovalDetailPane.tsx` และ tests, `DecisionDialog.tsx` และ tests หรือ dialog เฉพาะ D หากช่วยให้ props ชัดเจน, `MissionDecision.integration.test.tsx`, `mission_decisions.cy.ts`, shared freshness helper หากจำเป็น และเอกสาร validation หลีกเลี่ยงแก้ page/drawer ถ้า interface เดิมเพียงพอ

## Validation และจุดหยุด

ก่อน implement ตรวจ main อีกครั้ง ถ้าต่างจาก baseline ให้ประเมิน diff ก่อนทำงาน ต้องให้ Claude review แผน และบอสอนุมัติแผนพร้อม implementation ชัดเจน รอบนี้ยังไม่ติดตั้ง application dependencies หรือเริ่ม server ใน worktree D

หลัง implement รัน targeted governance/provider/Board tests, frontend full tests/coverage, ESLint, TypeScript, production build และ mocked Cypress dev/production โดยห้าม build พร้อม dev; ตรวจ docs links/lint และ git diff scope ถ้าต้องการ make check ใช้ Makefile จริงและรายงาน environment limitations แยกจาก FAIL

ไม่มี real decision mutation เพื่อทดสอบโดยไม่ได้รับอนุญาต หลัง local validation ให้ independent implementation review และ human acceptance จากนั้นค่อยขอ commit/push/Draft PR authorization ก่อนเผยแพร่ ตรวจ CI/CodeQL และ review exact head ก่อนขอ Ready/merge authorization แยกต่างหาก
