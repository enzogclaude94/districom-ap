import { z } from "zod";

// Zod probes `new Function` when a schema is built, to decide whether to compile validators. Under the Content
// Security Policy (no 'unsafe-eval') that probe is a violation, even though Zod catches it, so validation runs
// without code generation (R9-5-5). Importing `z` from here guarantees the setting comes first.
z.config({ jitless: true });

export { z };
