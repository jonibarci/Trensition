import { ComponentFixture, TestBed } from '@angular/core/testing';

import { Qa } from './qa';

describe('Qa', () => {
  let component: Qa;
  let fixture: ComponentFixture<Qa>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Qa]
    })
    .compileComponents();

    fixture = TestBed.createComponent(Qa);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
