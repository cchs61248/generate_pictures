/** 空狀態吉祥物：復古相機（避免昆蟲輪廓） */
export function WelcomeMascot({ className }: { className?: string }) {
  return (
    <div className={className} aria-hidden>
      <svg
        width="140"
        height="120"
        viewBox="0 0 140 120"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <ellipse cx="70" cy="108" rx="48" ry="8" fill="#4A3B32" opacity="0.08" />
        <rect
          x="26"
          y="34"
          width="88"
          height="62"
          rx="20"
          fill="#F8C3CD"
          stroke="#4A3B32"
          strokeWidth="2.4"
        />
        <rect
          x="46"
          y="26"
          width="26"
          height="14"
          rx="7"
          fill="#FCEB9C"
          stroke="#4A3B32"
          strokeWidth="2"
        />
        <rect
          x="74"
          y="26"
          width="22"
          height="14"
          rx="7"
          fill="#A7D7C5"
          stroke="#4A3B32"
          strokeWidth="2"
        />
        <circle cx="70" cy="66" r="21" fill="#FFFCF9" stroke="#4A3B32" strokeWidth="2.4" />
        <circle cx="70" cy="66" r="11" fill="#A7D7C5" stroke="#4A3B32" strokeWidth="2" />
        <circle cx="65" cy="62" r="2.3" fill="#FFFCF9" />
        <circle cx="54" cy="78" r="2.8" fill="#4A3B32" />
        <circle cx="86" cy="78" r="2.8" fill="#4A3B32" />
        <circle cx="55" cy="77" r="1.1" fill="#FFFCF9" />
        <circle cx="87" cy="77" r="1.1" fill="#FFFCF9" />
        <path
          d="M62 86c2.4 3 13.6 3 16 0"
          stroke="#4A3B32"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <circle cx="100" cy="50" r="4.5" fill="#FCEB9C" stroke="#4A3B32" strokeWidth="1.8" />
        <path
          d="M38 90l-8 8M102 90l8 8"
          stroke="#4A3B32"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    </div>
  )
}
