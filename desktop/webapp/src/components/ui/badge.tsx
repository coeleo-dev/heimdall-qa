import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded border px-1.5 py-px text-[10px] font-medium leading-4 whitespace-nowrap",
  {
    variants: {
      variant: {
        default: "border-border bg-secondary text-secondary-foreground",
        muted: "border-transparent bg-muted text-muted-foreground",
        outline: "border-border text-foreground",
        pass: "border-status-pass/35 bg-status-pass/15 text-status-pass",
        fail: "border-status-fail/40 bg-status-fail/15 text-status-fail",
        warn: "border-status-warn/40 bg-status-warn/15 text-status-warn",
        skip: "border-border bg-muted text-status-skip",
        http: "border-status-http/40 bg-status-http/15 text-status-http",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "span";
  return <Comp className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
export type BadgeProps = VariantProps<typeof badgeVariants>;
