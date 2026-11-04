import "@typespec/http";
import "@typespec/openapi";
import "@typespec/openapi3";

import "./models/common.tsp";
import "./models/documents.tsp";
import "./models/extractions.tsp";
import "./models/suggestions.tsp";
import "./models/links.tsp";
import "./models/experiments.tsp";
import "./models/corpus.tsp";
import "./models/review.tsp";
import "./routes/v1.tsp";

using TypeSpec.Http;
using TypeSpec.OpenAPI;

@service(#{ title: "DocLink API" })
@info(#{ version: "1.0.0" })
@server("http://localhost:8000", "Local development server")
namespace DocLink;
