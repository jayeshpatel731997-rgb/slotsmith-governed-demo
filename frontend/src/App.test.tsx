import { render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { App } from './App'

vi.stubGlobal('fetch', vi.fn((url:string)=>Promise.resolve({ok:true,json:()=>Promise.resolve(url.includes('audit')?[]:{scenario:'post-promo',locations:3200,skus:2000,assignments:2000,version:1,aisles:[]})})))

test('renders the governance boundary and optimizer action', async()=>{
  render(<App />)
  expect(screen.getByText(/synthetic · non-commercial/i)).toBeInTheDocument()
  expect(await screen.findByRole('button',{name:/optimize current drift/i})).toBeEnabled()
  expect(screen.getByText(/nothing moves until approval/i)).toBeInTheDocument()
})
