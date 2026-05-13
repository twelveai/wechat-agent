type IconProps = {
  className?: string;
};

const icons = {
  activity: "M22 12h-4l-3 7L9 5l-3 7H2",
  alert: "M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  arrowRight: "M5 12h14m-6-6 6 6-6 6",
  calendar: "M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z",
  check: "m5 12 4 4L19 6",
  clock: "M12 6v6l4 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  database: "M4 6c0-2 4-3 8-3s8 1 8 3-4 3-8 3-8-1-8-3Zm0 0v12c0 2 4 3 8 3s8-1 8-3V6m-16 6c0 2 4 3 8 3s8-1 8-3",
  fileText: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6M8 13h8M8 17h5",
  filter: "M3 5h18M6 12h12M10 19h4",
  help: "M9.1 9a3 3 0 1 1 5.8 1c-.8 1.2-2.9 1.3-2.9 3m0 4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  message: "M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z",
  quote: "M9 7H5a2 2 0 0 0-2 2v4h4v4h4V9a2 2 0 0 0-2-2Zm12 0h-4a2 2 0 0 0-2 2v4h4v4h4V9a2 2 0 0 0-2-2Z",
  refresh: "M21 12a9 9 0 0 1-15 6.7L3 16m0 0v5h5M3 12A9 9 0 0 1 18 5.3L21 8m0 0V3h-5",
  search: "m21 21-4.3-4.3M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Z",
  server: "M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Zm0 9a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Zm3-7h.01M7 17h.01",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z",
  sparkles: "M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3Zm6 10 .9 2.1L21 16l-2.1.9L18 19l-.9-2.1L15 16l2.1-.9L18 13ZM5 14l1.1 2.9L9 18l-2.9 1.1L5 22l-1.1-2.9L1 18l2.9-1.1L5 14Z",
  target: "M12 2v4m0 12v4M2 12h4m12 0h4M19.1 4.9l-2.8 2.8M7.7 16.3l-2.8 2.8M4.9 4.9l2.8 2.8m8.6 8.6 2.8 2.8M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm13 10v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
};

export type IconName = keyof typeof icons;

export function Icon({ name, className = "h-4 w-4" }: IconProps & { name: IconName }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      <path d={icons[name]} />
    </svg>
  );
}
