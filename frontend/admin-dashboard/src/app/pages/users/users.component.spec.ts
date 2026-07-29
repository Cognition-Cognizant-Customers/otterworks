import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { RouterTestingModule } from '@angular/router/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { UsersComponent } from './users.component';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';

describe('UsersComponent', () => {
  let component: UsersComponent;
  let fixture: ComponentFixture<UsersComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
    imports: [UsersComponent,
        RouterTestingModule,
        NoopAnimationsModule],
    providers: [provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting()]
}).compileComponents();

    fixture = TestBed.createComponent(UsersComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading state', () => {
    expect(component.loading).toBeTrue();
  });

  it('should load users', fakeAsync(() => {
    fixture.detectChanges();
    tick(700);
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.dataSource.data.length).toBeGreaterThan(0);
  }));

  it('should have correct displayed columns', () => {
    expect(component.displayedColumns).toEqual([
      'displayName', 'role', 'status', 'department', 'lastLogin', 'actions',
    ]);
  });

  it('should apply text filter', fakeAsync(() => {
    fixture.detectChanges();
    tick(700);
    fixture.detectChanges();
    const event = { target: { value: 'alice' } } as unknown as Event;
    component.applyFilter(event);
    expect(component.dataSource.filter).toBe('alice');
  }));

  it('should display page title', fakeAsync(() => {
    fixture.detectChanges();
    tick(700);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('User Management');
  }));
});
