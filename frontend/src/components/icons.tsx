import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  );
}

export function TrashIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
      <path d="M10 11v5M14 11v5" />
    </Icon>
  );
}

export function MenuIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </Icon>
  );
}

export function XIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 6l12 12M18 6L6 18" />
    </Icon>
  );
}

export function ArrowUpIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 19V5M6 11l6-6 6 6" />
    </Icon>
  );
}

export function ArrowUpRightIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M7 17L17 7M9 7h8v8" />
    </Icon>
  );
}

export function DatabaseIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <ellipse cx="12" cy="5.5" rx="7" ry="3" />
      <path d="M5 5.5v13c0 1.66 3.13 3 7 3s7-1.34 7-3v-13" />
      <path d="M5 12c0 1.66 3.13 3 7 3s7-1.34 7-3" />
    </Icon>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4 4" />
    </Icon>
  );
}

export function FileTextIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v4h4M10 12h5M10 16h5" />
    </Icon>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 12.5l4.5 4.5L19 7" />
    </Icon>
  );
}

export function AlertIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 4l9 16H3z" />
      <path d="M12 10v4M12 17h.01" />
    </Icon>
  );
}

export function CopyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="8" y="8" width="12" height="12" rx="2" />
      <path d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2" />
    </Icon>
  );
}

export function RefreshIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M20 11a8 8 0 10-2.34 5.66" />
      <path d="M20 4v7h-7" />
    </Icon>
  );
}

export function ArrowLeftIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M19 12H5M11 6l-6 6 6 6" />
    </Icon>
  );
}

export function ChevronRightIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9 5l7 7-7 7" />
    </Icon>
  );
}

export function LayersIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3l9 5-9 5-9-5z" />
      <path d="M3 13l9 5 9-5" />
    </Icon>
  );
}

export function BookOpenIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 5.5A2.5 2.5 0 016.5 3H20v16H6.5A2.5 2.5 0 004 21.5z" />
      <path d="M4 18.5A2.5 2.5 0 016.5 16H20" />
    </Icon>
  );
}

export function LogOutIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M15 4h3a2 2 0 012 2v12a2 2 0 01-2 2h-3" />
      <path d="M10 17l-5-5 5-5M5 12h10" />
    </Icon>
  );
}

export function DocumentIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v4h4M10 12h5M10 16h5" />
    </Icon>
  );
}

export function LinkIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71" />
    </Icon>
  );
}

export function ChevronsLeftIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M11 6l-6 6 6 6M18 6l-6 6 6 6" />
    </Icon>
  );
}

export function ChevronsRightIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M13 6l6 6-6 6M6 6l6 6-6 6" />
    </Icon>
  );
}

export function FilingMark(props: IconProps) {
  return (
    <Icon strokeWidth={1.4} {...props}>
      <path d="M9 19V5h7M9 12h5.5" />
    </Icon>
  );
}
