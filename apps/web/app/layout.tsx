import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Norm - AI Operations Control',
  description: 'AI-powered operations assistant for hospitality',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
