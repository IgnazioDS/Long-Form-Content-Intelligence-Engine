import { Suspense } from "react";

import AskPageClient from "./ask-page-client";

export default function AskPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <div className="text-sm text-muted-foreground">Loading...</div>
        </div>
      }
    >
      <AskPageClient />
    </Suspense>
  );
}
