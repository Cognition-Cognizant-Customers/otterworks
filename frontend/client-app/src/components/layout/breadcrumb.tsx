import { Link } from "react-router-dom";
import { ChevronRight, Home } from "lucide-react";

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

interface BreadcrumbProps {
  items: BreadcrumbItem[];
}

export function Breadcrumb({ items }: BreadcrumbProps) {
  return (
    <nav className="flex items-center gap-1 text-sm text-gray-500" aria-label="Breadcrumb">
      <Link
        to="/dashboard"
        className="hover:text-gray-700 transition p-1"
        aria-label="Home"
      >
        <Home size={16} />
      </Link>
      {items.map((item) => (
        <span key={item.href ?? item.label} className="flex items-center gap-1">
          <ChevronRight size={14} className="text-gray-400" />
          {item.href ? (
            <Link
              to={item.href}
              className="hover:text-gray-700 transition px-1"
            >
              {item.label}
            </Link>
          ) : (
            <span className="text-gray-900 font-medium px-1">
              {item.label}
            </span>
          )}
        </span>
      ))}
    </nav>
  );
}
