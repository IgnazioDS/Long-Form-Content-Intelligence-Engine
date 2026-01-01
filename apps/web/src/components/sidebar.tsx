"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const navItems = [
  { href: "/sources", label: "Sources" },
  { href: "/ask", label: "Ask" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex w-full flex-col border-b border-border/70 bg-white/70 px-6 py-5 backdrop-blur md:min-h-screen md:w-64 md:border-b-0 md:border-r">
      <div className="flex items-center justify-between md:block">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
            Long-Form Engine
          </p>
          <h1 className="mt-2 text-lg font-semibold leading-tight text-foreground">
            Content Intelligence
          </h1>
        </div>
      </div>
      <nav className="mt-6 flex gap-2 md:flex-col">
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "rounded-lg px-4 py-2 text-sm font-medium transition",
                isActive
                  ? "bg-primary text-primary-foreground shadow"
                  : "text-foreground/70 hover:bg-muted hover:text-foreground"
              )}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
