# Slice 5B — Checkpoint C: แผนพัฒนาหน้าจอการตัดสินใจของผู้ใช้

## สถานะและขอบเขตการอนุมัติ

อัปเดต 2026-09-11: บอสอนุมัติแผนฉบับล่าสุดและอนุญาตให้ implement พร้อมทดสอบแล้ว โดยยังห้าม commit, push หรือเปิด PR งานดำเนินการใน branch `codex/slice-5b-checkpoint-c-implementation` ดู [รายงาน implementation และ validation](2026-09-11-slice-5b-checkpoint-c-validation.md) สำหรับหลักฐานล่าสุด ข้อความด้านล่างที่กล่าวถึงรอบวางแผนเป็นบันทึกก่อนอนุมัติ

ประวัติรอบวางแผน — 2026-09-11 แปลจากร่างล่าสุดเป็นภาษาไทย โดยคงข้อกำหนดเดิม Claude ให้ผล PASS กับร่างก่อนหน้าและมีข้อเสนอแนะที่ไม่บล็อก 3 ข้อ รายละเอียดที่เพิ่มภายหลังยังไม่ได้รับการตรวจซ้ำจาก Claude การแปลนี้ไม่ถือเป็นการอนุมัติแผนหรือ implementation

ในรอบวางแผนเดิม บอสอนุญาตให้สร้างโฟลเดอร์ใหม่และวางแผนเท่านั้น ต่อมาได้รับอนุญาต implementation และการทดสอบตามบันทึกด้านบน การส่ง approval mutation จริง, commit, push, เปิด PR, merge และ Checkpoint D ยังอยู่นอกขอบเขตงานนี้

- Repository: `Mhaizza/ai-space-colony-mission-control`
- โฟลเดอร์ใหม่: `C:\Users\Mhaiz\Projects\ai-space-colony-mission-control-checkpoint-c`
- Branch สำหรับวางแผน: `codex/slice-5b-checkpoint-c-plan`
- รุ่นที่ตรวจ: `main @ 49b2f25392a7eae155b88206e47ae060ca06a31b` ซึ่งรวม PR #28 แล้ว
- เป็น clone แยกบน C: มี Git ของตัวเอง ไม่ใช้ Git metadata ร่วมกับ recovery checkout เก่าบน G:
- เอกสารหลัก: [แบบ Mission Operations UX](../specs/2026-08-18-slice-5b-mission-operations-ux-design.md)
- พฤติกรรมการอ่านที่ต้องรักษา: [แบบ Checkpoint B](../specs/2026-08-19-slice-5b-checkpoint-b-mission-governance-drawer-design.md)

ก่อน implement ต้องดึงสถานะ main ใหม่ ตรวจส่วนต่างจาก baseline นี้ ยืนยัน API contract และได้รับอนุญาตให้ implement โดยชัดแจ้ง การอนุมัติแผนอย่างเดียวไม่ใช่การอนุญาตให้ implement และการอนุมัติ merge เป็นอีกขั้นหนึ่ง หาก baseline เปลี่ยนจนกระทบแผน ต้องปรับแผนก่อนลงมือ

## เป้าหมายและขอบเขต

ผู้ใช้ที่ยืนยันตัวตนและมีสิทธิ์สามารถเลือก approval ของ Mission กด Approve หรือ Reject ระบุเหตุผลได้ตามต้องการ ยืนยันการส่ง แล้วเห็นรายละเอียดและรายการล่าสุดจาก backend การกดปุ่ม Approve/Reject ครั้งแรกเปิดหน้าต่างยืนยันเท่านั้น ยังไม่ส่ง decision

Checkpoint C รองรับการตัดสินใจครั้งแรกเท่านั้น แสดงปุ่มเมื่อรายละเอียดล่าสุดระบุครบว่า `status === "pending"`, `can_decide === true` และ `current_principal_decision === null` หากมี decision เดิม ให้แสดงข้อมูลต่อไป แต่ไม่มีปุ่ม Change decision ใน C เงื่อนไขเหล่านี้ใช้ควบคุมการแสดงผล ส่วน server ต้องตรวจสิทธิ์ทุกครั้งที่ส่ง

นอกขอบเขต: supersede/change decision ของ D, สร้าง approval, route ใหม่, backend/API/schema/migration, แก้หรือสร้าง generated client ใหม่, แก้ ADR-23, จัดการ role/trust/policy, AI decision, การเขียน GitHub, global approval inbox, เปลี่ยนความหมายของ Board approval, dependency ใหม่ และออกแบบ pagination ใหม่

## หลักฐานจาก repository

ตรวจไฟล์และ symbol ด้านล่างที่ baseline ข้างต้น หลักฐานนี้เป็นการอ่านโค้ด ไม่ใช่ผลรัน tests

| ส่วนที่ตรวจ | สิ่งที่มีอยู่และผลต่อแผน |
| --- | --- |
| `frontend/src/components/mission/governance/ApprovalDetailPane.tsx` | เรียก generated hook สำหรับอ่านรายละเอียด ปัจจุบันคืน error panel ก่อนอ่าน cached data หากโหลดข้อมูลหลังส่งไม่สำเร็จ ต้องเก็บรายละเอียดเดิมไว้พร้อมระบุว่าข้อมูลเก่าหรือเกิดข้อผิดพลาด |
| `frontend/src/components/mission/governance/ApprovalListPane.tsx` | เรียก list hook ด้วย Mission 3 ฟิลด์ ต้องรักษา identity และการเรียงรายการ ปัจจุบันแสดง error ก่อน cached items เช่นกัน |
| `frontend/src/components/mission/governance/MissionGovernanceDrawer.tsx` | จัดการ selection, reset เมื่อเปลี่ยน Mission, ปิด drawer และ Back ในจอแคบ เมื่อปิด drawer จะ unmount |
| `frontend/src/app/mission/page.tsx` | แสดง drawer ตามเงื่อนไข จึงวาง decision controller ขนาดเล็กไว้ที่ระดับ page เพื่อให้คงอยู่เมื่อปิดแล้วเปิด drawer ระหว่างที่ page ยัง mounted |
| `frontend/src/api/generated/mission-approvals/mission-approvals.ts` | มี `useSubmitApprovalDecisionApiV1MissionApprovalsRequestIdDecisionsPost` รับ `{ requestId, data, headers }` ให้ตั้ง `mutation: { retry: false }` โดยตรง เพื่อรักษาข้อห้าม automatic POST retry แม้ค่าเริ่มต้นของ QueryProvider เปลี่ยนในอนาคต |
| `frontend/src/api/generated/model/submitDecisionRequest.ts` | Body คือ `{ decision: "approve" | "reject", reason?: string | null }` ไม่ส่ง principal, role, trust, status หรือ Mission identity ใน body |
| `frontend/src/api/generated/model/submitApprovalDecisionApiV1MissionApprovalsRequestIdDecisionsPostHeaders.ts` | บังคับมี `"Idempotency-Key": string` |
| Generated mutation response | HTTP 200 ห่อ `ApprovalDecisionResponse` ด้วย `{ data, status, headers }` ต้องตรวจ status ก่อนถือว่าสำเร็จ |
| `frontend/src/api/mutator.ts` | Non-2xx โยน `ApiError` ที่มี `status` และ `data` ข้อผิดพลาดแบบมีโครงสร้างอยู่ที่ `data.detail.code/message` ส่วน `error.message` อาจเป็นข้อความทั่วไป ความผิดพลาดด้าน network หรือการอ่าน response อาจไม่ใช่ ApiError |
| `backend/app/api/mission_approvals.py::submit_approval_decision` | มี POST `/{request_id}/decisions` อยู่แล้ว ใช้ผู้ใช้ที่ยืนยันตัวตน บังคับ key และคืน decision response |
| `backend/app/mission/approval_service.py::submit_decision` | Lock request และตรวจ actor จากนั้น reserve/replay operation ก่อนเช็ก pending/decision เดิม key และ payload เดิม replay ผลที่ commit แล้วได้ ส่วน key ใหม่ไม่สามารถเขียนทับ effective decision เดิม |
| `backend/app/mission/approval_errors.py` | มี 403 ด้านสิทธิ์, 404 `approval_request_not_found`, 409 `approval_request_terminal`, `approval_decision_exists`, `idempotency_key_reused` |
| `frontend/src/components/providers/QueryProvider.tsx` | Mutation retry เป็น 0 ส่วน query retry เป็น 1 และ refetch เมื่อกลับมา focus ห้ามถือว่าสิทธิ์จาก cache ยังคงเดิมตลอดเวลาที่เปิด dialog |
| `frontend/src/components/ui/dialog.tsx` | มี Radix Dialog พร้อม title/description, focus, เนื้อหาเลื่อนได้ และ z-50 สูงกว่า drawer z-40 ใช้ยืนยันการส่งได้ แต่ไม่ใช้แทน drawer ที่เป็น non-modal |
| Tests เดิมของ B | Detail test ยืนยันว่าไม่มี mutation controls และ drawer test ยืนยันว่าไม่เริ่ม mutation hook ต้องปรับข้อห้ามแบบครอบจักรวาลให้เป็นเงื่อนไขการเรียกและสิทธิ์เฉพาะ C พร้อมคงข้อห้าม create/supersede |

## การแบ่งหน้าที่ของ component ที่เสนอ

- เพิ่ม `DecisionDialog.tsx` และ tests ใน `frontend/src/components/mission/governance/` สำหรับแสดงเป้าหมายที่ตรึงไว้ ตัวเลือก เหตุผล การยืนยัน ความคืบหน้า error และ retry โดยรองรับ accessibility และไม่คำนวณ business rules ของ backend
- เพิ่ม `useMissionDecision.ts` และ tests ในโฟลเดอร์เดียวกัน ใช้ generated mutation และ query-key helpers จัดการ snapshot ของ intent ที่ยืนยันแล้ว ป้องกันกดซ้ำแบบ synchronous ผลลัพธ์ และการโหลดข้อมูลหลังส่ง
- สร้าง controller ครั้งเดียวใน `/mission/page.tsx` เหนือจุดแสดง drawer ตามเงื่อนไข เก็บ operation ที่ยืนยันแล้วในหน่วยความจำของ page session โดยอ้าง request ID และ Mission tuple ที่บันทึกไว้ ล้างเมื่อ page unmount หรือ auth session เปลี่ยน ห้ามบันทึกเหตุผลหรือ key ลง browser storage
- ส่ง controller ผ่าน `MissionGovernanceDrawer` ไป `ApprovalDetailPane` ร่างใน dialog ต้องแยกและ reset ตาม request/Mission ที่เลือก ส่ง Mission context โดยไม่ parse URL
- ใช้ generated `getGetApprovalDetailApiV1MissionApprovalsRequestIdGetQueryKey(requestId)` และ `getListApprovalsApiV1MissionApprovalsGetQueryKey(params)` เพื่อจัดการ cache เฉพาะเป้าหมาย ใช้ object ตัวกรอง 3 ฟิลด์เดียวกับ list ห้ามประกอบ query-key string เอง
- คง global QueryProvider, shared mutator และ Dialog primitive เดิม ตรวจโครงสร้าง error เฉพาะจุดด้วย `unknown` ไม่ใช้ `any` หรือเขียนระบบ fetch กลางใหม่

ไฟล์ที่คาดว่าจะแก้: mission page และ tests, drawer และ tests, detail pane และ tests รวมถึง list pane และ tests เฉพาะการเก็บข้อมูลเดิมเมื่อ refetch ล้มเหลว ไฟล์ใหม่คือ dialog/controller และ tests หากต้องขยายชุดไฟล์ ต้องอธิบายเหตุผลในการ review

## ลำดับใช้งานและวงจรของ intent

1. เปิด dialog Approve/Reject เฉพาะรายการที่มีสิทธิ์และมี request/Mission identity ตรงกับ selection แสดง Mission, action, decision ที่เลือก และช่องเหตุผล หากยกเลิกก่อนยืนยัน ต้องไม่มี mutation และยังไม่สร้าง key
2. เมื่อกด Confirm ให้ดู snapshot ล่าสุดที่ reactive ของ detail query ไม่ใช้ snapshot ตอนเปิด dialog ต้องมี identity ตรงกัน เคยอ่านข้อมูลใหม่สำเร็จใน page/auth session ปัจจุบัน ไม่มี read error หรือ refetch ที่กำลังทำงาน สถานะ pending, can_decide=true และไม่มี current decision ไม่บังคับ GET เพิ่มทุกครั้งที่ Confirm เพราะ server ยังตรวจสิทธิ์ขณะ POST เพื่อรับมือการเปลี่ยนแปลงหลังเช็กนี้ หาก snapshot เก่าหรือมี error ให้บล็อกจน refresh สำเร็จ ห้ามถือว่าข้อมูลเก่าที่เก็บไว้แสดงผลใช้ยืนยันสิทธิ์ได้ เมื่อผ่านแล้วตรึง `{ requestId, Mission tuple, decision, reason }` และสร้าง key ด้วย `crypto.randomUUID()` หนึ่งครั้ง เหตุผลว่างให้เป็น null ส่วนข้อความที่ไม่ว่างเก็บตรงตามที่กรอก ทำ normalization ครั้งเดียวก่อนตรึง หากสร้าง UUID อย่างปลอดภัยไม่ได้ ให้แจ้ง error ในหน้าและไม่ส่ง ห้ามใช้ตัวสุ่มที่อ่อนแอแทน
3. ตั้ง guard แบบ synchronous ทันทีก่อนเริ่ม mutation เพื่อป้องกัน double-click สร้างสอง intent ก่อน React rerender ระหว่างส่งให้ตรึงช่องกรอก ปิดการส่งซ้ำ และไม่ให้ปิด dialog การซ่อน UI ไม่ใช่การยกเลิก request ที่ส่งไปแล้ว
4. Network error, response parsing failure หรือ 5xx ถือว่าผลยังไม่แน่นอน เก็บ key/payload เดิม แสดงข้อความว่าไม่สามารถยืนยันผลได้ พร้อม Retry intent เดิมที่ผู้ใช้ต้องกดเอง ห้าม automatic POST retry หรือสร้าง key ใหม่ในเส้นทางนี้
5. ระหว่างผลไม่แน่นอน ห้ามแก้ payload หรือเลือก decision ฝั่งตรงข้ามสำหรับ request นั้น ผู้ใช้ปิด error dialog ไปดู Mission อื่นได้ เมื่อกลับมาใน page เดิมต้องกู้ unresolved intent กลับมา ทุก retry ต้องเกิดจากการกด ใช้ target/payload/key เดิม และไม่ส่งภายใต้ auth session คนละชุด ข้อมูลใหม่จาก backend อาจช่วยยุติความไม่แน่นอนได้ หากมี current decision ให้แสดงตาม backend แต่ห้ามอนุมานว่าค่า decision/เหตุผลที่ตรงกันพิสูจน์ว่า intent นี้ถูกส่งสำเร็จ
6. หากได้รับการปฏิเสธ non-2xx ที่ยืนยันแน่ชัดว่า attempt ล้มเหลว ให้จบ attempt นั้น แล้ว refresh read state ที่เกี่ยวข้องเมื่อเป็นเรื่องสิทธิ์ ไม่พบข้อมูล หรือ conflict ปิดการตัดสินใจใหม่จนได้รายละเอียดที่มีสิทธิ์ล่าสุด `idempotency_key_reused` เป็น conflict ที่ต้องแจ้ง ไม่ใช่เหตุให้สร้าง key ใหม่แล้วส่งซ้ำเงียบ ๆ การยืนยันใหม่หลัง attempt ล้มเหลวแน่ชัดหรือยกเลิกร่างให้ได้ key ใหม่ ห้ามใช้ key เดิมกับ payload ที่เปลี่ยน ข้อนี้ไม่รวมผลไม่แน่นอนในข้อ 4
7. เมื่อยืนยัน HTTP 200 แล้ว ให้บันทึกว่า server รับการเขียนสำเร็จก่อนเริ่มอ่านใหม่ ห้ามคำนวณ status/quorum/effect ล่วงหน้า หรือถือว่าหนึ่ง approval ทำให้ request จบเสมอ Refetch detail และ filtered list แยกกันโดยใช้ identity ที่ตรึงไว้
8. หาก refresh ส่วนใดล้มเหลว ให้แสดงว่า decision ถูกบันทึกแล้วแต่ข้อมูลบางส่วนโหลดใหม่ไม่ได้ พร้อมปุ่ม Refresh สำหรับอ่านเท่านั้น ห้ามส่ง POST ซ้ำ ลบรายละเอียด/รายการเดิม หรือแสดงข้อความรวมว่า submission failed ปิดปุ่มส่งของ request นั้นจน detail refresh สำเร็จ
9. Response ที่กลับมาช้าต้องจัดการเฉพาะ query keys ของ request/Mission ที่ตรึงไว้ ห้ามปิด dialog ของรายการอื่น เปลี่ยน selection หรือแสดง success/error ของ Mission อื่น อัปเดต operation record ที่เกี่ยวข้อง ไม่ใช่รายการที่กำลังถูกเลือกในขณะนั้น
10. ไม่รับประกันการกู้ retry ข้ามการ reload ทั้งหน้า เปลี่ยน route หรือ auth session เมื่อกลับเข้าหน้า ต้องอ่านข้อมูลใหม่ก่อนเสนอ initial decision ข้อบังคับ backend ที่ให้หนึ่ง effective decision ต่อผู้ใช้เป็นแนวป้องกันซ้ำขั้นสุดท้าย ห้าม background retry หลัง session เปลี่ยน

ข้อเลือกเรื่องอายุของ state เหล่านี้ต้องได้รับการอนุมัติแผน Claude ต้องตรวจว่าการให้ page เป็นเจ้าของช่วยรักษา unresolved intent เมื่อปิด drawer ได้ โดยไม่กลายเป็นระบบคิวทั้งแอป

### วิธีตรวจ auth session ให้ชัดเจน

ใช้ wrapper เดิม `@/auth/clerk` ใน Clerk mode เปรียบเทียบคู่ `userId` และ `sessionId` เมื่อโหลดข้อมูลแล้วและ signed in การ refresh token ภายใน Clerk session เดิมไม่ถือเป็นการเปลี่ยน identity หาก identity หายหรือเปลี่ยน ให้ร่างและสิทธิ์ retry operation เดิมใช้ต่อไม่ได้

Local mode ใช้คู่นี้อย่างเดียวไม่ได้ เพราะ `frontend/src/auth/clerk.tsx::useAuth` คืน `local-user` / `local-session` คงที่ทุกครั้งที่มี token ให้เปรียบเทียบ `getLocalAuthToken()` กับ credential ที่เก็บเป็น private reference ของ auth context ปัจจุบันใน page อ่านซ้ำแบบ synchronous ทุก Confirm/Retry เพราะ helper ไม่ reactive จึงพึ่ง React effect อย่างเดียวไม่ได้ หาก token หายหรือเปลี่ยน ให้ยกเลิก context เดิมก่อน dispatch เก็บ credential เฉพาะ private reference ในหน่วยความจำ ห้ามใส่ใน operation records, query/mutation keys, logs, UI หรือ browser storage เพิ่มเติม

ตรวจ context ที่ตรึงไว้อีกครั้งหลังขั้นตอน asynchronous และก่อน dispatch การเลือก credential ของ request ต้องผูกกับ context ที่ตรวจแล้ว ไม่ส่ง intent เก่าในชื่อผู้ใช้ที่เพิ่ง login ใหม่โดยเงียบ ๆ Implementation ต้องพิสูจน์เงื่อนไขนี้ที่ขอบเขต generated request โดยใช้ request-options ที่มีอยู่ ไม่แก้ shared mutator หรือสร้าง auth mechanism ใหม่ หากทำไม่ได้ภายในชุดไฟล์ที่เสนอ ให้หยุดปรับแผนแทนการลด guard Request ที่ส่งไปแล้วอาจจบภายหลัง ให้ละเว้น UI callbacks ของ session เก่าและห้ามนำข้อมูลเฉพาะผู้ใช้จากผลนั้นเข้าสู่ session ใหม่ ต้องอ่านข้อมูลใหม่ใน session ที่เข้ามาแทน

Tests ของ Task 2/4 ต้องครอบคลุมเปลี่ยนผู้ใช้ Clerk, ผู้ใช้เดิมแต่ session ใหม่, refresh token ใน session เดิม, ลบ/เปลี่ยน local token แม้ wrapper IDs คงที่ และเปลี่ยน auth ระหว่างรอขั้นตอน asynchronous ใช้ credential สมมติใน tests เท่านั้น Guard นี้ป้องกันการส่ง intent ในนามคนผิด ไม่ใช่การคำนวณสิทธิ์แทน server

## การจัดการ error และการโหลดข้อมูลหลังส่ง

| ผลที่ได้รับ | พฤติกรรมหน้าจอและการดำเนินการที่เสนอ |
| --- | --- |
| 401 / 403 | อธิบายปัญหา login/สิทธิ์ ปิดการส่งและอ่านใหม่ด้วยสิทธิ์ปัจจุบัน ห้ามอนุมาน role หรือส่งอัตโนมัติหลัง login |
| 404 ไม่พบ request | แจ้งว่าไม่พบรายการและ refresh list ที่เกี่ยวข้อง ไม่ให้ตัดสินใจจาก detail เก่า |
| 409 terminal / มี decision แล้ว | แจ้ง conflict จาก server แล้ว refresh detail/list ไม่มีทางลัด supersede ใน C |
| 409 key reused | อธิบาย conflict อย่างปลอดภัย หยุด attempt ไม่สร้าง key ใหม่เพื่อ retry อัตโนมัติ |
| 400 / 422 | อธิบาย validation error ที่รู้จักหรือข้อความสำรองที่ปลอดภัย เก็บเหตุผลเพื่อแก้ไขและให้ยืนยันใหม่ |
| Network / 5xx / success body ผิดรูปแบบ | ถือว่าผลส่งยังไม่แน่นอน เก็บ intent เดิมสำหรับ manual retry |
| HTTP 200 แต่โหลดข้อมูลใหม่ล้มเหลว | รับทราบว่าเขียนแล้ว retry เฉพาะการอ่านแต่ละส่วน |

ใช้ตารางข้อความตาม error code เฉพาะจุด ห้าม render error object ซ้อน arbitrary หรือข้อความ server ดิบที่อาจมี key การ invalidate/refetch ต้องตรวจ failure โดยชัดเจนผ่าน rejected promise หรือ error result การ invalidate resolve ไม่ได้พิสูจน์ว่า refresh สำเร็จ ต้องทดสอบด้วย QueryClient จริงและ response ที่ควบคุมเวลาคืนผลได้

## งานที่จะทำหลังได้รับอนุญาต

ทุกงานต้องมีหลักฐาน RED จาก test ที่ล้มเหลวตรงประเด็นก่อน แล้ว implement ให้น้อยที่สุดจน GREEN ตรวจเฉพาะส่วนและตรวจ scope ห้ามทำ implementation หลายงานล่วงหน้าก่อนเขียน failing tests ของงานนั้น

### Task 1 — หน้าต่างยืนยันและเงื่อนไขสิทธิ์

เพิ่ม dialog tests สำหรับข้อความ approve/reject, เหตุผลที่ไม่บังคับ, keyboard focus, accessible name/description, cancel แล้วไม่มี POST และไม่สร้าง key ก่อน confirm เพิ่มตารางทดสอบสิทธิ์ใน detail สำหรับ pending/can_decide/current decision, terminal states, ไม่มีข้อมูล/loading/error และ identity ไม่ตรง ปรับข้อห้ามแบบครอบจักรวาลของ B เฉพาะจุดที่ C ตั้งใจเปลี่ยน

### Task 2 — Controller สำหรับส่งและตรึง intent

พิสูจน์ arguments/header ที่ส่งให้ generated mutation, หนึ่ง key ต่อ confirmation, กัน double-click, ไม่มี automatic retry, retry ใช้ payload เดิม, intent ใหม่ภายหลังได้ key แยก และจัดการ unknown error อย่างระมัดระวัง พิสูจน์ว่าการเปลี่ยน current decision/terminal/capability ขณะเปิด dialog บล็อก submission ใหม่ คง tests ที่ห้ามเรียก create/supersede

### Task 3 — อัปเดต cache และแยก failure ให้ถูกประเภท

ใช้ integration tests กับ QueryClient จริงและ deferred requests เพื่อพิสูจน์การ refresh detail/list เฉพาะเป้าหมายหลัง HTTP 200, แสดงค่าจาก backend, refresh แต่ละส่วนล้มเหลวแยกกันได้, เก็บข้อมูลเดิม และ Refresh หลังเขียนสำเร็จใช้ GET เท่านั้น ไม่ invalidate cache ทั้งระบบหรือส่ง POST ซ้ำจาก retry callbacks

### Task 4 — Selection และวงจรชีวิตของหน้า

ทดสอบเปลี่ยน Mission, เปลี่ยน approval, Back ในจอแคบ, ปิด/เปิด drawer และ page/session teardown ทั้งช่วง draft, ผลไม่แน่นอน และ response กลับช้า ตรวจว่าใช้ target/query keys เดิม รายการอื่นต้องไม่รับเหตุผล สิทธิ์ error หรือ success state จากรายการก่อน เมื่อกลับมา unresolved request ใน page เดิมยังใช้ key เดิม และหลัง reload ต้องอ่าน detail ใหม่ก่อนเสนอ decision ใหม่

### Task 5 — Regression และตรวจหน้าจอจริง

รักษา tests ของ B เรื่อง read states, ordering, selection reset, terminal data และ narrow drill-in คง Board regression และข้อห้ามเรียก create/supersede ตรวจ dialog บน desktop/mobile และด้วย keyboard การจำลอง browser ใช้แสดง interaction ได้แต่ต้องระบุว่า mocked การส่ง decision กับ backend จริงต้องได้รับอนุญาตชัดเจนและใช้ข้อมูล development ที่ทิ้งได้ ห้ามสร้าง approval ใน production หรือส่ง governance decision จริงเพียงเพื่อทดสอบแผน

## คำสั่งตรวจสอบสำหรับช่วง implementation ภายหลัง

รันจาก `frontend/`:

```text
npx vitest run src/components/mission/governance/ src/app/mission/page.test.tsx
npx vitest run src/components/BoardApprovalsPanel.test.tsx
npm run lint
npx tsc -p tsconfig.json --noEmit
npm run test
npm run build
```

จาก repository root รัน `git diff --check`, `make docs-check` และ `make check` ใน environment ที่รองรับ ตรวจ Makefile/CI ปัจจุบันก่อน เพราะ `make check` รวม backend coverage ด้วย ห้ามรัน build พร้อม next dev ไม่ต้อง api-gen บันทึก prerequisites ของ dependencies/services และแยกการตรวจที่รันไม่ได้เพราะ environment ออกจากผล FAIL รอบวางแผนนี้ยังไม่ได้รัน tests/build ของแอป

ก่อน merge ต้องมี CI และ CodeQL ครบทุกภาษาที่สัมพันธ์กับ head ปัจจุบันของ implementation PR กรณี analysis บน merge ref ให้ตรวจ head/base parents ไม่บังคับว่า analysis SHA ต้องเท่ากับ head SHA ต้องมี independent exact-head review, Human acceptance และ Human merge authorization แยกต่างหาก

## รายการตรวจและการส่งต่องานในทีม

- บอสกำหนด UX อนุมัติ scope ทดลองรับงาน และอนุมัติ merge
- Codex ตรวจหลักฐาน repository, implement ตามขอบเขตเมื่อได้รับอนุญาต และรายงาน validation evidence
- Claude ตรวจร่างแผนเทียบ parent design และโค้ดแบบอิสระ รายงาน blocking findings พร้อมไฟล์/symbol และข้อเสนอแก้ การ review ไม่ใช่การอนุญาตให้ implement
- ตรวจว่า retry ของการส่งที่ยังไม่แน่นอนยังใช้งานได้เมื่อ backend capability เปลี่ยน โดยไม่เปิดทางให้สร้าง intent ใหม่ที่ไม่มีสิทธิ์
- ตรวจว่า POST ที่รับทราบว่าสำเร็จแล้วตามด้วย read failure ไม่ทำให้เกิด POST retry
- ตรวจว่าอายุ operation ในหน่วยความจำ การแยก auth session และเงื่อนไขข้อมูล cache ทำได้ด้วยขอบเขต auth/query ที่มีอยู่
- ตรวจว่าไม่ต้องเพิ่ม backend contract และ C ไม่รวมงาน D โดยเงียบ ๆ

ข้อกำหนดเรื่องอายุ controller และ error policy เป็นข้อเสนอใหม่ ไม่ใช่การอ้างว่าเคยได้รับอนุมัติแล้ว ต้องปิด blocking findings ก่อนอนุญาต implementation เอกสารนี้ไม่ได้รายงานความคืบหน้าว่า implement ส่วนใดเสร็จแล้ว

## ผลตรวจเอกสารในรอบวางแผน

ตอนสร้าง branch ใหม่ สถานะสะอาดที่ baseline นี้ หลังเขียนแผนมีเพียงไฟล์แผน untracked และไม่มี tracked-file modifications การตรวจ `python scripts/check_markdown_links.py` ผ่าน 36 ไฟล์ Markdown และ markdownlint-cli2 0.15.0 ตาม config repository ผ่าน 43 ไฟล์ ไม่มี error ไฟล์ใหม่ผ่าน `git diff --no-index --check` และ tracked diff ว่าง

ไม่ได้ติดตั้ง dependencies ของ frontend/backend ใช้ npx เฉพาะเครื่องมือตรวจเอกสาร ไม่มีการรัน application tests, build, เปิด service, ทำ mutation จริง, commit, push หรือเปิด PR ต้องตรวจเอกสารซ้ำหลังแปลก่อนส่งมอบ และรายงานผลจริงในแชต
