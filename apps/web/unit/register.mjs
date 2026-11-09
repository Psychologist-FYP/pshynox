// Unit tests only: let node resolve extensionless relative imports in app code ("./protocol" -> "./protocol.ts").
import { register } from "node:module";
register("./resolve-ts.mjs", import.meta.url);
