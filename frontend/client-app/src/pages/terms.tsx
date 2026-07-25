import { Link } from "react-router-dom";
import { Footer } from "@/components/layout/footer";
import { Logo } from "@/components/ui/logo";
import { COMPANY } from "@/lib/corporate";

export default function TermsPage() {
  return (
    <main className="min-h-screen bg-otter-50 flex flex-col">
      <header className="bg-white border-b border-gray-300">
        <div className="max-w-4xl mx-auto px-4 h-14 flex items-center gap-3">
          <Link to="/" className="flex items-center gap-2">
            <Logo size={28} />
            <span className="font-semibold text-gray-900">OtterWorks, Inc.</span>
          </Link>
        </div>
      </header>
      <div className="flex-1 max-w-4xl mx-auto w-full px-4 py-10">
        <h1 className="text-2xl font-bold text-gray-900 mb-1">Terms of Service</h1>
        <p className="text-xs text-gray-400 mb-6">
          Fictional legal document — {COMPANY.disclaimer}
        </p>
        <div className="bg-white border border-gray-300 rounded p-6 space-y-4 text-sm text-gray-600 leading-relaxed">
          <p>
            <strong className="text-gray-900">1. Acceptance of Terms.</strong>{" "}
            By accessing OtterWorks, you agree to conduct all clam-related and
            document-related commerce in accordance with the customs of the
            waterways. These terms are entirely fictional and carry no legal
            force anywhere, wet or dry.
          </p>
          <p>
            <strong className="text-gray-900">2. Use of the Service.</strong>{" "}
            You may store files, edit documents, and collaborate with other
            otters. You may not dam the service, hoard shared pebbles, or
            misrepresent your den affiliation.
          </p>
          <p>
            <strong className="text-gray-900">3. Accounts.</strong> You are
            responsible for keeping your password drier than your fur. Notify
            us of any unauthorized access to your holt immediately.
          </p>
          <p>
            <strong className="text-gray-900">4. Termination.</strong>{" "}
            OtterWorks, Inc. may suspend accounts that disturb the ecosystem.
            Suspended otters retain the right to appeal to the River Council.
          </p>
          <p>
            <strong className="text-gray-900">5. Governing Law.</strong> These
            terms are governed by the fictional statutes of Estuary Bay.
          </p>
        </div>
      </div>
      <Footer />
    </main>
  );
}
