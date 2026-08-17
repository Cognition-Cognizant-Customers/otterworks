import type { Collaborator } from "@/types";
import { getInitials, generateColor } from "@/lib/utils";
import { cn } from "@/lib/utils";

interface UserPresenceAvatarsProps {
  collaborators: Collaborator[];
  maxVisible?: number;
}

export function UserPresenceAvatars({
  collaborators,
  maxVisible = 5,
}: Readonly<UserPresenceAvatarsProps>) {
  const visible = collaborators.slice(0, maxVisible);
  const remaining = collaborators.length - maxVisible;

  if (collaborators.length === 0) return null;

  return (
    <div className="flex items-center gap-2">
      <div className="flex -space-x-2">
        {visible.map((collaborator) => (
          <div
            key={collaborator.userId}
            className={cn(
              "relative w-8 h-8 rounded-full border-2 border-white flex items-center justify-center text-xs font-bold text-white",
              "transition-transform hover:scale-110 hover:z-10"
            )}
            style={{
              backgroundColor: collaborator.color || generateColor(collaborator.userId),
            }}
            title={`${collaborator.name}${collaborator.isOnline ? " (online)" : ""}`}
          >
            {getInitials(collaborator.name)}
            {collaborator.isOnline && (
              <span className="absolute -bottom-0.5 -right-0.5 w-3 h-3 bg-green-500 rounded-full border-2 border-white" />
            )}
          </div>
        ))}
        {remaining > 0 && (
          <div className="w-8 h-8 rounded-full border-2 border-white bg-gray-200 flex items-center justify-center text-xs font-semibold text-gray-600">
            +{remaining}
          </div>
        )}
      </div>
      <span className="text-xs text-gray-500">
        {collaborators.filter((c) => c.isOnline).length} online
      </span>
    </div>
  );
}
