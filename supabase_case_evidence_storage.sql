-- 고소음이 사건별 증거자료 비공개 저장소
-- Supabase Dashboard > SQL Editor에서 이 파일 전체를 한 번 실행하세요.

insert into storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
values (
    'case-evidence',
    'case-evidence',
    false,
    10485760,
    array[
        'image/jpeg',
        'image/png',
        'image/webp',
        'image/heic',
        'image/heif',
        'application/pdf',
        'audio/mpeg',
        'audio/mp4',
        'audio/x-m4a',
        'audio/wav',
        'audio/x-wav'
    ]
)
on conflict (id) do update
set
    public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "case evidence select own files" on storage.objects;
create policy "case evidence select own files"
on storage.objects
for select
to authenticated
using (
    bucket_id = 'case-evidence'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
);

drop policy if exists "case evidence insert own files" on storage.objects;
create policy "case evidence insert own files"
on storage.objects
for insert
to authenticated
with check (
    bucket_id = 'case-evidence'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
);

drop policy if exists "case evidence delete own files" on storage.objects;
create policy "case evidence delete own files"
on storage.objects
for delete
to authenticated
using (
    bucket_id = 'case-evidence'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
);
