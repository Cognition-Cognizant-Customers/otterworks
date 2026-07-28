import { Footer } from "@/components/layout/footer";
import { Logo } from "@/components/ui/logo";
import {
  COMPANY,
  FOUNDING_STORY,
  LEADERSHIP,
  DEPARTMENTS,
  PRODUCTS,
  PRESS_RELEASES,
  CAREERS,
} from "@/lib/corporate";

export default function Home() {
  return (
    <main className="min-h-screen bg-otter-50 text-gray-800">
      {/* Top utility bar */}
      <header className="bg-white border-b border-gray-300">
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Logo size={36} />
            <div>
              <span className="block font-semibold text-gray-900 leading-tight">
                OtterWorks, Inc.
              </span>
              <span className="block text-[11px] uppercase tracking-wider text-gray-500">
                {COMPANY.tagline}
              </span>
            </div>
          </div>
          <nav className="flex items-center gap-2">
            <a
              href="/login"
              className="px-4 py-1.5 text-sm font-medium text-otter-600 border border-otter-600 rounded hover:bg-otter-50 transition"
            >
              Sign In
            </a>
            <a
              href="/register"
              className="px-4 py-1.5 text-sm font-medium bg-otter-600 text-white rounded hover:bg-otter-700 transition"
            >
              Create Account
            </a>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <div className="bg-otter-700">
        <div className="max-w-6xl mx-auto px-4 py-16 text-center">
          <div className="flex items-center justify-center gap-4 mb-5">
            <Logo size={64} className="bg-white p-1 rounded-sm" />
            <h1 className="text-4xl font-bold text-white">OtterWorks</h1>
          </div>
          <p className="text-lg text-otter-100 mb-2 max-w-2xl mx-auto">
            {COMPANY.tagline}. Dependable systems of record for every den —
            secure file storage, collaborative documents, and retail operations
            since {COMPANY.founded}.
          </p>
          <p className="text-xs text-otter-300 mb-8">{COMPANY.disclaimer}</p>
          <div className="flex items-center justify-center gap-3">
            <a
              href="/login"
              className="px-8 py-2.5 bg-white text-otter-700 rounded hover:bg-gray-100 transition font-semibold text-sm"
            >
              Sign In
            </a>
            <a
              href="/register"
              className="px-8 py-2.5 bg-accent-500 text-white rounded hover:bg-accent-600 transition font-semibold text-sm"
            >
              Create Account
            </a>
          </div>
        </div>
      </div>

      {/* Our Story */}
      <Section id="story" title="Our Story">
        <div className="max-w-3xl mx-auto space-y-4">
          {FOUNDING_STORY.map((paragraph) => (
            <p key={paragraph.slice(0, 32)} className="text-sm text-gray-600 leading-relaxed">
              {paragraph}
            </p>
          ))}
        </div>
      </Section>

      {/* Products */}
      <Section id="products" title="Products" alt>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {PRODUCTS.map((product) => (
            <Card key={product.name} title={product.name} body={product.description} />
          ))}
        </div>
      </Section>

      {/* Leadership */}
      <Section id="leadership" title="Leadership">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {LEADERSHIP.map((leader) => (
            <div key={leader.name} className="p-5 bg-white rounded border border-gray-300">
              <div className="w-10 h-10 rounded-full bg-otter-600 text-white flex items-center justify-center text-sm font-semibold mb-3">
                {leader.name.split(" ").map((part) => part[0]).join("")}
              </div>
              <h3 className="text-sm font-semibold text-gray-900">{leader.name}</h3>
              <p className="text-xs uppercase tracking-wider text-accent-600 mb-2">
                {leader.title}
              </p>
              <p className="text-xs text-gray-500 leading-relaxed">{leader.bio}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* Departments */}
      <Section id="departments" title="Departments" alt>
        <div className="overflow-x-auto">
          <table className="w-full bg-white border border-gray-300 text-sm">
            <thead>
              <tr className="bg-otter-50 text-left">
                <th className="px-4 py-2 border-b border-gray-300 text-xs uppercase tracking-wider text-gray-600">
                  Department
                </th>
                <th className="px-4 py-2 border-b border-gray-300 text-xs uppercase tracking-wider text-gray-600">
                  Charter
                </th>
              </tr>
            </thead>
            <tbody>
              {DEPARTMENTS.map((dept) => (
                <tr key={dept.name} className="border-b border-gray-200 last:border-b-0">
                  <td className="px-4 py-2 font-medium text-gray-900 whitespace-nowrap">
                    {dept.name}
                  </td>
                  <td className="px-4 py-2 text-gray-600">{dept.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {/* Press / Newsroom */}
      <Section id="press" title="Newsroom">
        <ul className="max-w-3xl mx-auto divide-y divide-gray-200 bg-white border border-gray-300 rounded">
          {PRESS_RELEASES.map((release) => (
            <li key={release.title} className="px-5 py-3">
              <p className="text-xs uppercase tracking-wider text-gray-400">
                {release.date}
              </p>
              <p className="text-sm font-medium text-gray-900">{release.title}</p>
            </li>
          ))}
        </ul>
      </Section>

      {/* Careers */}
      <Section id="careers" title="Careers" alt>
        <p className="text-center text-sm text-gray-600 mb-6 max-w-2xl mx-auto">
          Join a company where every otter counts. Open roles across our
          riverbank campuses (all positions fictional).
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {CAREERS.map((job) => (
            <div
              key={job.role}
              className="p-4 bg-white rounded border border-gray-300 flex items-center justify-between gap-4"
            >
              <div>
                <h3 className="text-sm font-semibold text-gray-900">{job.role}</h3>
                <p className="text-xs text-gray-500">
                  {job.team} · {job.location}
                </p>
              </div>
              <span className="text-xs font-medium text-otter-600 border border-otter-600 rounded px-3 py-1 whitespace-nowrap">
                Open
              </span>
            </div>
          ))}
        </div>
      </Section>

      <div className="bg-white border-t border-gray-300 py-6 text-center">
        <p className="text-xs text-gray-400">
          {COMPANY.name} · Headquartered at {COMPANY.headquarters} · Founded{" "}
          {COMPANY.founded}
        </p>
      </div>

      <Footer />
    </main>
  );
}

function Section({
  id,
  title,
  alt = false,
  children,
}: Readonly<{
  id: string;
  title: string;
  alt?: boolean;
  children: React.ReactNode;
}>) {
  return (
    <section id={id} className={alt ? "bg-white" : "bg-otter-50"}>
      <div className="max-w-6xl mx-auto px-4 py-14">
        <h2 className="text-xl font-bold text-gray-900 text-center mb-2">{title}</h2>
        <div className="w-10 h-0.5 bg-accent-500 mx-auto mb-8" />
        {children}
      </div>
    </section>
  );
}

function Card({ title, body }: Readonly<{ title: string; body: string }>) {
  return (
    <div className="p-5 bg-white rounded border border-gray-300">
      <h3 className="text-sm font-semibold text-gray-900 mb-1.5">{title}</h3>
      <p className="text-xs text-gray-500 leading-relaxed">{body}</p>
    </div>
  );
}
