import { FilingMark } from "@/components/icons";

const SIZES = {
  sm: { box: "h-8 w-8", mark: "h-3.5 w-3.5" },
  md: { box: "h-9 w-9", mark: "h-4 w-4" },
  lg: { box: "h-10 w-10", mark: "h-[18px] w-[18px]" },
} as const;

export function BrandMark({
  size = "md",
  className = "",
}: {
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const sizing = SIZES[size];

  return (
    <div
      aria-hidden="true"
      className={`flex shrink-0 items-center justify-center rounded-full border border-hairline bg-surface-soft text-lemon ${sizing.box} ${className}`}
    >
      <FilingMark className={sizing.mark} />
    </div>
  );
}
