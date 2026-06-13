import { statusLabel, type CompanionStatus } from "@/lib/protocol";

const STATUS_IMAGE: Record<CompanionStatus, string> = {
  offline: "/limeLogo.png",
  idle: "/limesimple.png",
  thinking: "/limeeThinking.png",
  working: "/limeBrain.png",
  approval: "/limeLogo.png",
  warning: "/limeLogo.png",
  celebrating: "/limeLogo.png",
};

type MascotBubbleProps = {
  status: CompanionStatus;
  avatarUrl?: string | null;
  botName?: string;
  showLabel?: boolean;
};

export function MascotBubble({
  status,
  avatarUrl,
  botName = "LimeBot",
  showLabel = true,
}: MascotBubbleProps) {
  const imageSrc = avatarUrl || STATUS_IMAGE[status];

  return (
    <div className={`mascot-bubble mascot-${status}`}>
      <div className="mascot-orbit" />
      <img className="mascot-image" src={imageSrc} alt={`${botName} avatar`} />
      {showLabel ? <span className="mascot-label">{statusLabel(status)}</span> : null}
    </div>
  );
}
