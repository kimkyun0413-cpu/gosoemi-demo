import { createClient } from "npm:@supabase/supabase-js@2";

// Deploy with JWT verification enabled. The function verifies the bearer token again.
Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
  const token = req.headers.get("authorization")?.match(/^Bearer (.+)$/i)?.[1];
  if (!token) return new Response("Unauthorized", { status: 401 });

  const url = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !key) return new Response("Service Unavailable", { status: 503 });
  const admin = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: { user }, error: authError } = await admin.auth.getUser(token);
  if (authError || !user) return new Response("Unauthorized", { status: 401 });

  try {
    const storage = admin.storage.from("case-evidence");
    // List all objects under the authenticated user's own prefix, including orphan folders.
    async function removeFolder(folder: string): Promise<void> {
      let offset = 0;
      while (true) {
        const { data, error } = await storage.list(folder, { limit: 100, offset });
        if (error) throw error;
        const entries = data ?? [];
        const files = entries.filter((entry) => entry.id).map((entry) => folder + "/" + entry.name);
        if (files.length) {
          const { error: removeError } = await storage.remove(files);
          if (removeError) throw removeError;
        }
        for (const entry of entries.filter((entry) => !entry.id)) {
          await removeFolder(folder + "/" + entry.name);
        }
        if (entries.length < 100) break;
        // Deleted objects alter pagination; restart at zero when files were removed.
        offset = files.length ? 0 : offset + 100;
      }
    }
    await removeFolder(user.id);

    for (const table of ["cases", "consent_records"]) {
      const { error } = await admin.from(table).delete().eq("user_id", user.id);
      if (error) throw error;
    }
    const { error: deleteError } = await admin.auth.admin.deleteUser(user.id);
    if (deleteError) throw deleteError;
    return new Response(JSON.stringify({ deleted: true }), {
      status: 200, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch (error) {
        console.error("Account deletion failed");
    return new Response("Deletion failed", { status: 500 });
  }
});