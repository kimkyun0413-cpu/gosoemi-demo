-- 후기 검토 운영자 지정. 적용 SQL을 실행한 뒤 한 번 실행한다.
-- <운영자 user id>를 Streamlit 설정 ADMIN_USER_IDS에 있는 값(관리자 계정의 Supabase 사용자 ID)으로 바꾼다.
-- 바꾸지 않고 실행하면 형식 오류로 아무것도 바뀌지 않는다. 결과에 user_id 한 줄이 나오면 지정된 것이다.

insert into public.review_admins (user_id)
select id from auth.users where id = '<운영자 user id>'::uuid
on conflict do nothing
returning user_id;
